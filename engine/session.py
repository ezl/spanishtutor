"""
Post-onboarding session handling: open, content loop, close.
Phase control for new_skill sessions is deterministic — code selects the prompt
per phase based on turn count. Luz executes one scoped job per turn.
"""
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm
from .curriculum import LEVEL_ORDER, get_skill, next_new_skill, next_new_vocab_skill

GOODBYE_WORDS = {'bye', 'adiós', 'adios', 'chao', 'hasta luego', 'goodbye', 'ciao', 'nos vemos'}
INACTIVITY_TIMEOUT_MINUTES = 60

# ── Phase control constants ────────────────────────────────────────────────────

GRAMMAR_PHASE_TURNS = {'guided_practice': 4, 'free_production': 3, 'reinforcement': 4, 'assessment': 3}
VOCAB_PHASE_TURNS   = {'guided_practice': 5, 'free_production': 2, 'reinforcement': 5, 'assessment': 3}

GRAMMAR_PHASE_FLOW = ['present', 'questions', 'guided_practice', 'free_production', 'reinforcement_check', 'assessment', 'complete']
VOCAB_PHASE_FLOW   = ['present', 'guided_practice', 'free_production', 'reinforcement_check', 'assessment', 'complete']

CLARIFYING_QUESTIONS_STRING = (
    'What questions do you have about this, if any? '
    'If you have questions I\'ll answer them before we proceed to testing your knowledge. '
    'If you have no questions, just say "no questions" or "start quizzing".'
)

REINFORCEMENT_CHECK_STRING = (
    'Are you ready to do a quick assessment quiz, or would you like to practice a bit more to reinforce?'
)

NO_QUESTIONS_PHRASES = {
    'no questions', 'no question', 'start quizzing', 'start quiz',
    'no', 'none', 'nope', 'ready', 'listo', 'start', 'all good', 'got it', 'good',
}
QUIZ_READY_PHRASES = {
    'quiz', 'ready', 'assessment', 'yes', 'si', 'sí', 'listo', 'start',
    'start quizzing', 'all good', 'let\'s go', 'dale',
}
MORE_PRACTICE_PHRASES = {
    'practice', 'more', 'more practice', 'practice more', 'reinforce',
    'keep going', 'continue', 'not yet', 'more practice please', 'no',
}

# ── New skill prompts (opening / present phase) ────────────────────────────────

GRAMMAR_PRESENT_PROMPT = """You are teaching {skill_name} to a Spanish student.

Skill description: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Write the lesson content now. Include ALL of the following in this exact order:

1. Rule — what this is and when to use it. 1-2 sentences, plain language.
2. Paradigm — the complete conjugation table or pattern structure. Format it so it scans fast.
3. Examples — 2-3 natural sentences using the student's interests. Label each ✓
4. Wrong example — one common mistake with this skill, corrected. Format: ❌ [wrong] → ✓ [correct] — one line explaining why.

200-300 words total. Chat style, not textbook. No bold headers. Stop after the wrong example — the system appends a follow-up prompt after your message."""

VOCAB_PRESENT_PROMPT = """You are teaching {skill_name} to a Spanish student.

Skill description: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Write the lesson content now. Include ALL of the following in this exact order:

1. Word list — each item with its English translation. If any items are verbs, include the most common conjugated forms.
2. Usage examples — one example sentence per item using the student's interests where natural. Label each ✓
3. Contrast notes — only where there is a genuine confusion risk (e.g. grande vs gordo). Skip if not needed.

200-300 words total. Chat style, not textbook. No bold headers."""

# ── Phase system suffixes (injected into system prompt for continuation turns) ─

QUESTIONS_PHASE_SUFFIX = """CURRENT PHASE: Questions about {skill_name}

The lesson you just taught is in the conversation above. The student may ask questions.

Your job:
- Answer questions about {skill_name} or how it compares to other Spanish grammar or vocabulary
- After every answer, end with exactly: "Anything else, or shall we start?"
- If the question is completely off-topic (not about Spanish language learning), say briefly: "I'm here to answer questions about {skill_name} right now — anything else, or shall we start?"
- Do NOT run drills. Do NOT move to practice on your own. Wait for the student to say they are ready."""

GUIDED_PRACTICE_GRAMMAR_SUFFIX = """CURRENT PHASE: Guided Practice — {skill_name}

Your job: run ONE drill targeting {skill_name}. Then after the student responds, briefly confirm or correct and give the next drill.

Drill format by level:
- A1-A2: fill-in-the-blank (provide the sentence with a blank and the infinitive in parentheses)
- B1-B2: transformation (give a sentence, ask them to rewrite it using {skill_name})
- C1+: translation or production with a specific constraint

Rules:
- ONE drill per turn. No preamble, no praise.
- After student response: correct or confirm in ONE line only, then the next drill immediately.
- Never hint. Never show the answer before they try.
- Use student interests in examples where natural."""

GUIDED_PRACTICE_VOCAB_SUFFIX = """CURRENT PHASE: Guided Practice — {skill_name}

Your job: run ONE vocabulary recall drill. Alternate EN→ES and ES→EN.

Rules:
- ONE item per turn. No preamble.
- After student response: confirm or correct in ONE line, then next drill immediately.
- Check the history to avoid repeating items already tested.
- Never show the answer before they try."""

FREE_PRODUCTION_SUFFIX = """CURRENT PHASE: Free Production — {skill_name}

Your job: prompt the student to produce language using {skill_name} with NO scaffolding — no blanks, no sentence starters, no examples to copy.

Rules:
- Give a fresh scenario each turn. Draw from student interests.
- After they produce: correct significant errors using the standard correction format. Confirm what they did right in ONE line.
- ONE prompt per turn."""

ASSESSMENT_SUFFIX = """CURRENT PHASE: Assessment — {skill_name}

Your job: test {skill_name} directly. Clean questions only — no hints, no scaffolding, no examples in the question.

Rules:
- ONE question per turn. Vary the format across turns.
- After student response: confirm or correct in ONE line. Nothing else.
- No praise, no encouragement. Neutral and brief."""

# ── Other session prompt templates ────────────────────────────────────────────

SRS_REVIEW_PROMPT = """The student is starting a review session.

Skills due for spaced repetition review:
{skill_list}

Session instructions:
- Brief warm greeting, then straight into the first question. No preamble.
- One retrieval question per skill. Vary formats: translate, fill-in-blank, produce a sentence.
- Use the student's interests in examples wherever natural: {interests}
- After each response: confirm or correct briefly, then move on immediately.
- After all skills are covered: ask "Want to do one more quick set to lock it in?"

Generate the opening message and first question now."""

CONVERSATION_PROMPT = """The student is starting a free conversation session.

Student level: {cefr_level}
Student interests: {interests}
Last session: {last_summary}

Session instructions:
- Open with a genuine question about their life. Draw from their interests or last session.
- Let conversation flow naturally for 4-6 exchanges.
- Correct significant errors inline using the standard correction format.
- Minor errors (accents, small typos): ignore, never interrupt flow.
- Close with one thing they did well and one thing to watch next time.

Generate the opening question now. Spanish if B1+, English if A1-A2."""

READING_PROMPT = """The student is starting a reading comprehension session.

Student level: {cefr_level}
Student interests: {interests}

Session instructions:
- Generate a short reading text in Spanish. Length: 100-150 words for B1, up to 250 for C1+.
  Topic must connect directly to the student's interests.
- Tell them to read it and say "listo" when done.
- Ask 2-3 comprehension questions (real understanding — not just translation).
- Pick one word from the text and ask them to use it in a new sentence.
- Close with a brief note on what grammar or vocabulary the text was practicing.

Generate the opening message introducing the text, then present the text."""

WRITING_PROMPT = """The student is starting a writing session.

Student level: {cefr_level}
Student interests: {interests}

Session instructions:
- Give a writing prompt tied to their interests, calibrated to level:
    A1: 2-3 sentences in present tense
    A2: short paragraph using past tense
    B1: paragraph mixing preterite and imperfect
    B2+: short opinion piece with discourse markers
- After they write: correct the top 2-3 errors only. Don't overwhelm.
- Ask them to rewrite one corrected sentence.
- Close with specific feedback on what improved.

Generate the opening message with the writing prompt now."""


# ── Phase helpers ──────────────────────────────────────────────────────────────

def _skill_type(skill) -> str:
    return 'vocab' if skill and 'vocab' in skill.skill_id else 'grammar'


def _phase_max_turns(phase: str, stype: str) -> int:
    turns = VOCAB_PHASE_TURNS if stype == 'vocab' else GRAMMAR_PHASE_TURNS
    return turns.get(phase, 0)


def _next_phase(phase: str, stype: str) -> str:
    flow = VOCAB_PHASE_FLOW if stype == 'vocab' else GRAMMAR_PHASE_FLOW
    try:
        idx = flow.index(phase)
        return flow[idx + 1] if idx + 1 < len(flow) else 'complete'
    except ValueError:
        return 'complete'


def _is_no_questions(text: str) -> bool:
    return text.strip().lower() in NO_QUESTIONS_PHRASES


def _wants_quiz(text: str) -> bool:
    return text.strip().lower() in QUIZ_READY_PHRASES


def _wants_more_practice(text: str) -> bool:
    return text.strip().lower() in MORE_PRACTICE_PHRASES


async def _set_phase(session, phase: str, turns: int) -> None:
    from learner.models import Session
    await sync_to_async(
        lambda: Session.objects.filter(pk=session.pk).update(
            current_phase=phase, phase_turns_completed=turns
        )
    )()
    session.current_phase = phase
    session.phase_turns_completed = turns


def _get_phase_suffix(phase: str, skill, stype: str, events: list) -> str:
    name = skill.name if skill else 'this skill'
    if phase == 'questions':
        return QUESTIONS_PHASE_SUFFIX.format(skill_name=name)
    if phase in ('guided_practice', 'reinforcement'):
        if stype == 'grammar':
            return GUIDED_PRACTICE_GRAMMAR_SUFFIX.format(skill_name=name)
        return GUIDED_PRACTICE_VOCAB_SUFFIX.format(skill_name=name)
    if phase == 'free_production':
        return FREE_PRODUCTION_SUFFIX.format(skill_name=name)
    if phase == 'assessment':
        return ASSESSMENT_SUFFIX.format(skill_name=name)
    return ''


def _build_new_skill_history(events: list) -> list:
    """
    Build Anthropic-compatible message history for new_skill sessions.
    events[0].content is always the lesson (present phase).
    Subsequent events are continuation exchanges.
    """
    history = [{"role": "user", "content": "[lesson]"}]
    for e in events:
        if e.content:
            history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    return history


# ── Decision tree ──────────────────────────────────────────────────────────────

async def _select_session(user):
    """Returns (session_type, context, frontier_skills)."""
    from learner.models import SkillScore, Session
    from .scoring import get_frontier_skills, check_win_state

    now = timezone.now()
    level = user.estimated_cefr_level or 'A1'
    level_idx = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 0

    if await check_win_state(user):
        return 'conversation', {'win_state': True}, []

    frontier_skills = await get_frontier_skills(user)

    scores = await sync_to_async(
        lambda: list(SkillScore.objects.filter(user=user, mode='writing').select_related('skill'))
    )()

    def _sid(s):
        return s.skill.skill_id if s.skill else ''

    # 1. SRS Review — highest priority if 3+ skills overdue
    due = [s for s in scores if s.score > 0 and s.next_review_at and s.next_review_at <= now]
    due.sort(key=lambda s: s.next_review_at)
    if len(due) >= 3:
        frontier_ids = {fs.skill_id for fs in frontier_skills} if frontier_skills else set()
        due_in_frontier = [s for s in due if _sid(s) in frontier_ids]
        due_pool = due_in_frontier if len(due_in_frontier) >= 3 else due
        due_skill_objs = [d for s in due_pool[:6] if (d := get_skill(_sid(s)))]
        if due_skill_objs:
            return 'srs_review', {'due_skills': due_skill_objs}, frontier_skills

    # 2. Conversation — B1+ and not in last 3 sessions
    if level_idx >= 2:
        recent_types = await sync_to_async(
            lambda: list(
                Session.objects.filter(user=user, ended_at__isnull=False)
                               .exclude(session_type='onboarding')
                               .order_by('-ended_at')
                               .values_list('session_type', flat=True)[:3]
            )
        )()
        if 'conversation' not in recent_types:
            return 'conversation', {}, frontier_skills

    # 3. Reading — B1+ and reading mode significantly underscored
    if level_idx >= 2:
        reading_scores = await sync_to_async(
            lambda: list(SkillScore.objects.filter(user=user, mode='reading').select_related('skill'))
        )()
        if reading_scores and scores:
            reading_avg = sum(s.score for s in reading_scores) / len(reading_scores)
            writing_avg = sum(s.score for s in scores) / len(scores)
            if reading_avg < writing_avg - 1.0:
                return 'reading', {}, frontier_skills

    # 4. Default: push a new skill
    scored_ids = {_sid(s) for s in scores}
    skill = next_new_skill(level, scored_ids)
    return 'new_skill', {'skill': skill}, frontier_skills


# ── Session lifecycle ──────────────────────────────────────────────────────────

def _is_goodbye(text: str) -> bool:
    return text.strip().lower() in GOODBYE_WORDS


async def _check_inactivity(user, session) -> bool:
    last_event = await sync_to_async(
        lambda: session.events.order_by('-timestamp').first()
    )()
    if not last_event:
        return False
    return (timezone.now() - last_event.timestamp).total_seconds() > INACTIVITY_TIMEOUT_MINUTES * 60


async def handle_session(user, text: str, attachments: list = None) -> dict:
    from learner.models import Session

    if _is_goodbye(text):
        return await _close_session(user, explicit=True)

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .select_related('target_skill')
                               .first()
    )()

    if session and await _check_inactivity(user, session):
        await _close_session_record(session, user)
        session = None

    if not session:
        return await _open_session(user, text)

    if session.session_type == 'new_skill':
        return await _continue_new_skill(user, session, text)

    return await _continue_session(user, session, text, attachments)


async def _open_session(user, text: str) -> dict:
    from learner.models import Session, SessionEvent, SessionSkill, Skill

    session_type, context, frontier_skills = await _select_session(user)

    last_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=False)
                               .exclude(session_type='onboarding')
                               .order_by('-ended_at').first()
    )()
    last_summary = last_session.summary[:120] if last_session and last_session.summary else "this is their first session"

    session = await sync_to_async(Session.objects.create)(
        user=user, session_type=session_type
    )

    # Write SessionSkill frontier pool
    if frontier_skills:
        for skill in frontier_skills:
            await sync_to_async(SessionSkill.objects.get_or_create)(session=session, skill=skill)

    level = user.estimated_cefr_level or 'A1'
    interests = user.interests or "daily life, work, food, exercise, friends and family"

    win_state = context.get('win_state', False)
    if win_state:
        prompt = (
            f"Student level: {level}. Send them a genuine win message: "
            f"'You won! You've gone as far as we can take you. We consider you at mastery for core Spanish skills.' "
            f"Then ask: 'Would you like to do a full deep review of all skills? We'll run a comprehensive assessment covering every skill and mode.'"
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    elif session_type == 'new_skill':
        skill = context.get('skill')
        if not skill:
            prompt = CONVERSATION_PROMPT.format(
                cefr_level=level, interests=interests, last_summary=last_summary
            )
            opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        else:
            stype = 'vocab' if 'vocab' in skill['id'] else 'grammar'
            if stype == 'grammar':
                prompt = GRAMMAR_PRESENT_PROMPT.format(
                    skill_name=skill['name'],
                    skill_description=skill['description'],
                    cefr_level=level,
                    interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                initial_phase = 'questions'
            else:
                prompt = VOCAB_PRESENT_PROMPT.format(
                    skill_name=skill['name'],
                    skill_description=skill['description'],
                    cefr_level=level,
                    interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                initial_phase = 'guided_practice'

            skill_obj = await sync_to_async(Skill.objects.get)(skill_id=skill['id'])
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    target_skill=skill_obj,
                    current_phase=initial_phase,
                    phase_turns_completed=0,
                )
            )()

    elif session_type == 'srs_review':
        skill_list = "\n".join(
            f"- {s['name']}: {s['description']}" for s in context['due_skills']
        )
        prompt = SRS_REVIEW_PROMPT.format(skill_list=skill_list, interests=interests)
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    elif session_type == 'conversation':
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    elif session_type == 'reading':
        prompt = READING_PROMPT.format(cefr_level=level, interests=interests)
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    elif session_type == 'writing':
        prompt = WRITING_PROMPT.format(cefr_level=level, interests=interests)
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    else:
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='conversation',
        content=opening,
        user_response='',
    )

    return {"text": opening, "audio_url": None, "session_ended": False}


# ── New skill phase control ────────────────────────────────────────────────────

async def _continue_new_skill(user, session, text: str) -> dict:
    from learner.models import SessionEvent

    skill = session.target_skill
    stype = _skill_type(skill)
    phase = session.current_phase
    turns = session.phase_turns_completed

    # Auto-close when assessment is complete
    if phase == 'complete':
        return await _close_session(user, explicit=False)

    # Load events
    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:40])
    )()

    # Record student response — update user_response only, preserve lesson content
    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    # Build history including lesson content
    history = _build_new_skill_history(events)

    # ── Keyword-exit phases ──
    if phase == 'questions':
        if _is_no_questions(text):
            await _set_phase(session, 'guided_practice', 0)
            phase = 'guided_practice'
        # else: stay in questions, generate answer below

    elif phase == 'reinforcement_check':
        if _wants_quiz(text):
            await _set_phase(session, 'assessment', 0)
            phase = 'assessment'
        elif _wants_more_practice(text):
            await _set_phase(session, 'reinforcement', 0)
            phase = 'reinforcement'
        else:
            # Unrecognised input — re-send the check
            await sync_to_async(SessionEvent.objects.create)(
                session=session, event_type='conversation',
                content=REINFORCEMENT_CHECK_STRING, user_response='',
            )
            return {"text": REINFORCEMENT_CHECK_STRING, "audio_url": None, "session_ended": False}

    # ── Generate response for current phase ──
    suffix = _get_phase_suffix(phase, skill, stype, events)
    response_text = await call_llm(history, user=user, system_suffix=suffix)

    # ── Advance turn count for counted phases ──
    if phase != 'questions':
        new_turns = turns + 1
        max_turns = _phase_max_turns(phase, stype)

        if new_turns >= max_turns:
            next_p = _next_phase(phase, stype)
            await _set_phase(session, next_p, 0)
            # Append reinforcement check to response when transitioning to it
            if next_p == 'reinforcement_check':
                response_text = response_text + "\n\n" + REINFORCEMENT_CHECK_STRING
        else:
            await _set_phase(session, phase, new_turns)

    # Create new event for this response
    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='conversation',
        content=response_text,
        user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Standard session continuation ─────────────────────────────────────────────

async def _continue_session(user, session, text: str, attachments: list = None) -> dict:
    from learner.models import SessionEvent

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:20])
    )()

    history = []
    for e in events:
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
            history.append({"role": "assistant", "content": e.content})

    history.append({"role": "user", "content": text})

    response_text = await call_llm(history, user=user)

    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(
                user_response=text, content=response_text
            )
        )()
    else:
        await sync_to_async(SessionEvent.objects.create)(
            session=session,
            event_type='conversation',
            content=response_text,
            user_response=text,
        )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Session close ──────────────────────────────────────────────────────────────

async def _close_session_record(session, user):
    """Close a session silently (inactivity timeout). Run scoring + interests."""
    from learner.models import Session
    from .interests import extract_and_store_interests
    from .scoring import score_session

    await sync_to_async(
        lambda: Session.objects.filter(pk=session.pk).update(ended_at=timezone.now())
    )()
    try:
        await score_session(session, user)
    except Exception:
        pass
    try:
        await extract_and_store_interests(session, user)
    except Exception:
        pass


async def _close_session(user, explicit: bool = True) -> dict:
    import django.conf
    from learner.models import Session
    from .interests import extract_and_store_interests
    from .scoring import score_session

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .first()
    )()

    summary_prompt = """The student is ending the session. Generate a warm closing message from Luz Angela calibrated to their level:
- Brief summary of what was covered today
- One specific thing they did well
- What to work on next session
- Warm goodbye

Keep it to 4-5 sentences."""

    summary = await call_llm([{"role": "user", "content": summary_prompt}], user=user)

    dev_log = None

    if session:
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                ended_at=timezone.now(),
                summary=summary[:500],
            )
        )()

        facts = await extract_and_store_interests(session, user)

        scored = []
        try:
            scored = await score_session(session, user)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("score_session failed: %s", exc)

        if django.conf.settings.DEV_MODE:
            parts = []
            if facts:
                interest_lines = [
                    f"  {f['topic']} ({f['category']}, {f['confidence']}) {'[new]' if f.get('new') else '[reinforced]'}"
                    for f in facts
                ]
                parts.append("[dev] interests:\n" + "\n".join(interest_lines))
            else:
                parts.append("[dev] no interests extracted")

            if scored:
                score_lines = [f"  {s['skill_id']} → {s['score']}" for s in scored]
                parts.append("[dev] scores:\n" + "\n".join(score_lines))
            else:
                parts.append("[dev] no skills scored")

            dev_log = "\n".join(parts)

    return {"text": summary, "audio_url": None, "session_ended": True, "dev_log": dev_log}
