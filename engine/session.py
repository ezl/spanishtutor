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

SECOND_PASS_CHECK_STRING = (
    'That covers everything — want to do a quick second pass to lock it in?'
)
SECOND_PASS_PHRASES = {
    'yes', 'sure', 'yeah', 'yep', 'ok', 'okay', 'listo', 'sí', 'si',
    "let's go", 'dale', 'second pass', 'go again', 'again',
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

SCORE_TO_QUESTIONS = {0: 2, 1: 3, 2: 2, 3: 1, 4: 1}

SRS_REVIEW_PROMPT = """The student is starting a spaced repetition review session.

Skills to review: {skill_list}

Open with 2-3 sentences: tell the student we're reviewing these skills, and that you'll ask one to three questions per skill depending on how well they know each one.

Then immediately ask ONE retrieval question for the first skill: "{first_skill_name}"

Vary the format: translate, fill-in-blank, or produce a sentence. No hints. Draw from student interests where natural: {interests}"""

SRS_REVIEW_SAME_SKILL_SUFFIX = """CURRENT TASK: SRS Review

Evaluate the student's last answer in ONE line. Then ask question {next_q_num} of {total_q} for "{skill_name}".

Use a different format than the last question (vary: translate, fill-in-blank, produce a sentence). No hints. No preamble."""

SRS_REVIEW_NEXT_SKILL_SUFFIX = """CURRENT TASK: SRS Review

Evaluate the student's last answer in ONE line. Then move to the next skill:

"{next_skill_name}" — {next_skill_description}

Ask question 1 of {total_q}. Vary format: translate, fill-in-blank, produce a sentence. No hints. No preamble."""

SRS_REVIEW_COMPLETE_SUFFIX = """CURRENT TASK: SRS Review — all skills covered

Evaluate the student's last answer in ONE line only (correct or confirm). Stop there — do not add anything else."""

CONVERSATION_TURNS = 6

CONVERSATION_PROMPT = """The student is starting a free conversation session.

Student level: {cefr_level}
Student interests: {interests}
Last session: {last_summary}

Open with a genuine question about their life. Draw from their interests or last session.
Let the conversation flow naturally.
Correct significant errors inline using the standard correction format.
Minor errors (accents, small typos): ignore, never interrupt flow.

Generate the opening question now. Spanish if B1+, English if A1-A2."""

CONVERSATION_CLOSE_SUFFIX = """CURRENT TASK: Conversation wrap-up

The conversation has reached its natural end. Close with 3-4 sentences:
1. One specific thing the student did well (grammar, vocabulary, or expression — be concrete, name it)
2. One specific thing to watch next time (equally concrete)
3. A warm goodbye

No generic praise. Be specific about what you actually observed in this conversation."""

READING_PHASE_TURNS = {'comprehension': 4, 'production': 2}

READING_PROMPT = """You are running a reading comprehension session.

Target skill: {skill_name}
Skill description: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Write a reading passage in Spanish that:
1. Naturally demonstrates {skill_name} multiple times throughout
2. Is calibrated to level: ~100 words at B1, ~150 at B2, up to 250 at C1+
3. Chooses ONE of these interests as the setting or subject: {interests}

After the passage, tell the student to read it and let you know when they're done. Do not ask any questions yet."""

READING_COMPREHENSION_SUFFIX = """CURRENT TASK: Reading Comprehension — question {q_num} of 4

The reading passage is in the conversation above. Ask ONE comprehension question that tests real understanding — not translation, not vocabulary lookup.

Rules:
- ONE question only. No preamble.
- After student responds: confirm or correct in ONE line only."""

READING_PRODUCTION_SUFFIX = """CURRENT TASK: Reading Production — turn {turn_num} of 2

Pick ONE word or phrase from the passage that uses {skill_name}. Ask the student to use it in a new sentence of their own (not copied from the text).

Rules:
- ONE word or phrase. No preamble.
- After student responds: confirm or correct usage in ONE line only.
- Turn 2: pick a different word or phrase than turn 1."""

READING_CLOSE_SUFFIX = """CURRENT TASK: Reading session wrap-up

Close with 3-4 sentences:
1. Name the skill the passage was practicing: {skill_name}
2. One specific thing the student did well in their comprehension or production answers
3. One thing to watch
4. Warm goodbye

Session ends after this message."""

WRITING_CORRECTION_TURNS = 6  # 3 errors × (rewrite + new sentence)

WRITING_PROMPT = """You are running a writing session.

Target skill: {skill_name}
Skill description: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Give the student ONE writing prompt that:
1. Requires them to use {skill_name} naturally
2. Invites them to share a personal experience, preference, or opinion — choose a topic that elicits real content from their life
3. Is calibrated to level: 2-3 sentences at A1, short paragraph at A2-B1, 4-6 sentences at B2+
4. Draws the topic from their interests: {interests}

State the prompt clearly. Do not write an example or start writing for them."""

WRITING_FIRST_ERROR_SUFFIX = """CURRENT TASK: Writing Correction — error 1 of up to 3

The student just submitted their writing (shown above). Read it carefully.

Find the most important error. Present:
1. The error sentence — quote it exactly
2. The corrected version
3. Why — one sentence

Ask them to rewrite that sentence. Nothing else."""

WRITING_REWRITE_SUFFIX = """CURRENT TASK: Writing Correction — rewrite done

The student just rewrote an error sentence. Confirm their rewrite in ONE line (correct or flag any remaining issue).

Then ask them to write a BRAND NEW sentence using the same grammatical pattern — completely fresh, not from their original text."""

WRITING_NEXT_ERROR_SUFFIX = """CURRENT TASK: Writing Correction — error {error_num} of up to 3

The student just wrote a new sentence. Confirm it in ONE line.

Then find error {error_num} from their ORIGINAL writing piece. Present:
1. The error sentence — quote it exactly
2. The corrected version
3. Why — one sentence

Ask them to rewrite it. If there aren't {error_num} distinct errors, give a stretch challenge instead: ask them to write a more complex sentence using the same pattern they've been practicing."""

WRITING_CLOSE_SUFFIX = """CURRENT TASK: Writing session wrap-up

Close with 3-4 sentences:
1. What the student did well overall
2. The main pattern they practiced today ({skill_name})
3. One thing to keep working on
4. Warm goodbye

Session ends after this message."""

# ── Session close summary prompts (English / Spanish variants) ────────────────

CLOSE_SUMMARY_PROMPT_EN = """The student is finishing a Spanish tutoring session. Write a warm closing message from Luz Angela:
- One sentence: what we covered today
- One sentence: one specific thing they did well
- One sentence: what to focus on next session
- One warm goodbye line

No lists. No headers. 4 sentences, conversational."""

CLOSE_SUMMARY_PROMPT_ES = """El estudiante está terminando la sesión de español. Escribe un mensaje de cierre cálido de Luz Angela:
- Una oración: lo que cubrimos hoy
- Una oración: algo específico que hicieron bien
- Una oración: en qué enfocarse la próxima vez
- Una despedida cálida

Sin listas. Sin encabezados. 4 oraciones en tono conversacional."""

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


def _srs_build_schedule(session_skills, score_map):
    """Returns [(skill_obj, question_count), ...] in due order."""
    return [(ss.skill, SCORE_TO_QUESTIONS.get(score_map.get(ss.skill.skill_id, 0), 2))
            for ss in session_skills]


def _srs_position(turns, schedule):
    """
    Returns (skill_obj, question_num_1indexed, total_for_skill) for the question
    at position `turns` in the flat question sequence, or None if past the end.
    """
    cumulative = 0
    for skill, q in schedule:
        if turns < cumulative + q:
            return skill, turns - cumulative + 1, q
        cumulative += q
    return None


async def _set_phase(session, phase: str, turns: int) -> None:
    from learner.models import Session
    await sync_to_async(
        lambda: Session.objects.filter(pk=session.pk).update(
            current_phase=phase, phase_turns_completed=turns
        )
    )()
    session.current_phase = phase
    session.phase_turns_completed = turns


ADVANCED_LEVELS = {'B1', 'B2', 'C1', 'C2'}


def _is_advanced(user) -> bool:
    return (user.estimated_cefr_level or 'A1') in ADVANCED_LEVELS


def _build_checkin(user, session_type: str, last_snippet: str, context: dict, target_name: str = None) -> str:
    """Return a scripted check-in message. English for A1/A2, Spanish for B1+."""
    advanced = _is_advanced(user)
    snippet = last_snippet or ('la última sesión' if advanced else 'your last session')

    if session_type == 'srs_review':
        due_count = len(context.get('due_skills', []))
        skills_word = f"{due_count} tema{'s' if due_count != 1 else ''}" if advanced else f"{due_count} skill{'s' if due_count != 1 else ''}"
        if advanced:
            return f"¡Hola! Última vez: {snippet}. Hoy quiero hacer un repaso — tienes {skills_word} para revisar. ¿Listo/a para empezar?"
        return f"Hey! Last time: {snippet}. Today I want to do a quick review — you have {skills_word} to practice. It helps lock things in. Ready?"

    elif session_type == 'new_skill':
        name = target_name or ('algo nuevo' if advanced else 'something new')
        if advanced:
            return f"¡Hola! Última vez: {snippet}. Hoy quiero avanzar con algo nuevo: {name}. ¿Listo/a?"
        return f"Hey! Last time: {snippet}. Today I want to push forward with something new: {name}. Ready to dive in?"

    elif session_type == 'conversation':
        if advanced:
            return f"¡Hola! Última vez: {snippet}. Hoy vamos a tener una charla libre. ¿Listo/a?"
        return f"Hey! Last time: {snippet}. Today let's have a free conversation session in Spanish. Ready when you are!"

    elif session_type == 'reading':
        name = target_name or ('lectura' if advanced else 'some reading')
        if advanced:
            return f"¡Hola! Última vez: {snippet}. Hoy vamos a trabajar en lectura con «{name}». ¿Listo/a?"
        return f"Hey! Last time: {snippet}. Today let's do some reading practice — we'll work with {name}. Ready?"

    elif session_type == 'writing':
        name = target_name or ('escritura' if advanced else 'some writing')
        if advanced:
            return f"¡Hola! Última vez: {snippet}. Hoy vamos a trabajar en escritura — específicamente {name}. ¿Listo/a?"
        return f"Hey! Last time: {snippet}. Today let's work on your writing — specifically {name}. Ready?"

    else:
        if advanced:
            return f"¡Hola! Última vez: {snippet}. ¿Listo/a para continuar?"
        return f"Hey! Last time: {snippet}. Ready to keep going?"


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
        _pool = due_pool[:6]
        due_skill_objs = await sync_to_async(
            lambda: [d for s in _pool if (d := get_skill(_sid(s)))]
        )()
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
    skill = await sync_to_async(next_new_skill)(level, scored_ids)
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

    if session.current_phase == 'check_in':
        return await _handle_check_in(user, session, text)

    if session.session_type == 'new_skill':
        return await _continue_new_skill(user, session, text)

    if session.session_type == 'srs_review':
        return await _continue_srs_review(user, session, text)

    if session.session_type == 'conversation':
        return await _continue_conversation(user, session, text)

    if session.session_type == 'reading':
        return await _continue_reading(user, session, text)

    if session.session_type == 'writing':
        return await _continue_writing(user, session, text)

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

    # Write SessionSkill pool — due skills for srs_review, frontier for everything else
    if session_type == 'srs_review':
        due_ids = [s['id'] for s in context.get('due_skills', [])]
        due_map = await sync_to_async(
            lambda: {s.skill_id: s for s in Skill.objects.filter(skill_id__in=due_ids)}
        )()
        for sid in due_ids:
            if sid in due_map:
                await sync_to_async(SessionSkill.objects.get_or_create)(session=session, skill=due_map[sid])
    elif frontier_skills:
        for skill in frontier_skills:
            await sync_to_async(SessionSkill.objects.get_or_create)(session=session, skill=skill)

    level = user.estimated_cefr_level or 'A1'
    interests = user.interests or "daily life, work, food, exercise, friends and family"
    win_state = context.get('win_state', False)

    # ── Pre-resolve target skill (needed for check-in message + opening content) ──

    target_skill_obj = None

    if session_type == 'new_skill' and not win_state:
        skill_dict = context.get('skill')
        if skill_dict:
            target_skill_obj = await sync_to_async(Skill.objects.get)(skill_id=skill_dict['id'])
            _ts = target_skill_obj
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(target_skill=_ts)
            )()

    elif session_type == 'reading':
        from learner.models import SkillScore, Skill as SkillModel
        _w = await sync_to_async(
            lambda: {s.skill.skill_id: s.score
                     for s in SkillScore.objects.filter(user=user, mode='writing').select_related('skill')
                     if s.skill}
        )()
        _r = await sync_to_async(
            lambda: {s.skill.skill_id: s.score
                     for s in SkillScore.objects.filter(user=user, mode='reading').select_related('skill')
                     if s.skill}
        )()
        target_skill_obj = await sync_to_async(
            lambda: next(
                (s for s in SkillModel.objects.filter(active=True).order_by('order')
                 if _w.get(s.skill_id, 0) >= 1 and _r.get(s.skill_id, 0) < _w.get(s.skill_id, 0)),
                SkillModel.objects.filter(active=True).order_by('order').first()
            )
        )()
        if target_skill_obj:
            _ts = target_skill_obj
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(target_skill=_ts)
            )()
            await sync_to_async(SessionSkill.objects.get_or_create)(session=session, skill=target_skill_obj)

    elif session_type == 'writing':
        from learner.models import SkillScore, Skill as SkillModel
        _wm = await sync_to_async(
            lambda: {s.skill.skill_id: s.score
                     for s in SkillScore.objects.filter(user=user, mode='writing').select_related('skill')
                     if s.skill}
        )()
        target_skill_obj = await sync_to_async(
            lambda: next(
                (s for s in SkillModel.objects.filter(active=True).order_by('order')
                 if _wm.get(s.skill_id, 0) < 3),
                SkillModel.objects.filter(active=True).order_by('order').first()
            )
        )()
        if target_skill_obj:
            _ts = target_skill_obj
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(target_skill=_ts)
            )()
            await sync_to_async(SessionSkill.objects.get_or_create)(session=session, skill=target_skill_obj)

    # ── Check-in for returning users ──────────────────────────────────────────

    if last_session and not win_state:
        raw = last_session.summary or ''
        last_snippet = (raw[:80] + '…') if len(raw) > 80 else raw
        target_name = target_skill_obj.name if target_skill_obj else None
        checkin_text = _build_checkin(user, session_type, last_snippet, context, target_name)
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation',
            content=checkin_text, user_response='',
        )
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='check_in', phase_turns_completed=0
            )
        )()
        return {"text": checkin_text, "audio_url": None, "session_ended": False}

    # ── Generate opening content (first-time user, or win state) ─────────────

    if win_state:
        prompt = (
            f"Student level: {level}. Send them a genuine win message: "
            f"'You won! You've gone as far as we can take you. We consider you at mastery for core Spanish skills.' "
            f"Then ask: 'Would you like to do a full deep review of all skills? We'll run a comprehensive assessment covering every skill and mode.'"
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    elif session_type == 'new_skill':
        skill_dict = context.get('skill')
        if not skill_dict or not target_skill_obj:
            prompt = CONVERSATION_PROMPT.format(
                cefr_level=level, interests=interests, last_summary=last_summary
            )
            opening = await call_llm([{"role": "user", "content": prompt}], user=user)
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    current_phase='conversation', phase_turns_completed=0
                )
            )()
        else:
            stype = _skill_type(target_skill_obj)
            if stype == 'grammar':
                prompt = GRAMMAR_PRESENT_PROMPT.format(
                    skill_name=target_skill_obj.name,
                    skill_description=target_skill_obj.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                initial_phase = 'questions'
            else:
                prompt = VOCAB_PRESENT_PROMPT.format(
                    skill_name=target_skill_obj.name,
                    skill_description=target_skill_obj.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                initial_phase = 'guided_practice'
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    current_phase=initial_phase, phase_turns_completed=0
                )
            )()

    elif session_type == 'srs_review':
        due_skills = context.get('due_skills', [])
        skill_list = "\n".join(f"- {s['name']}: {s['description']}" for s in due_skills)
        first_skill_name = due_skills[0]['name'] if due_skills else 'the first topic'
        prompt = SRS_REVIEW_PROMPT.format(
            skill_list=skill_list, first_skill_name=first_skill_name, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='review', phase_turns_completed=0
            )
        )()

    elif session_type == 'conversation':
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='conversation', phase_turns_completed=0
            )
        )()

    elif session_type == 'reading':
        prompt = READING_PROMPT.format(
            skill_name=target_skill_obj.name if target_skill_obj else 'Spanish',
            skill_description=target_skill_obj.description if target_skill_obj else '',
            cefr_level=level, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        if target_skill_obj:
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    current_phase='present', phase_turns_completed=0
                )
            )()

    elif session_type == 'writing':
        prompt = WRITING_PROMPT.format(
            skill_name=target_skill_obj.name if target_skill_obj else 'Spanish',
            skill_description=target_skill_obj.description if target_skill_obj else '',
            cefr_level=level, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        if target_skill_obj:
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    current_phase='prompt', phase_turns_completed=0
                )
            )()

    else:
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=opening, user_response='',
    )
    return {"text": opening, "audio_url": None, "session_ended": False}


# ── Check-in handler ──────────────────────────────────────────────────────────

async def _handle_check_in(user, session, text: str) -> dict:
    """Student acknowledged the check-in. Generate the real session opening content."""
    from learner.models import Session, SessionEvent, SessionSkill

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:10])
    )()
    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()

    level = user.estimated_cefr_level or 'A1'
    interests = user.interests or "daily life, work, food, exercise, friends and family"
    last_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=False)
                               .exclude(session_type='onboarding')
                               .order_by('-ended_at').first()
    )()
    last_summary = last_session.summary[:120] if last_session and last_session.summary else "this is their first session"

    session_type = session.session_type
    skill = session.target_skill  # pre-set in _open_session

    if session_type == 'new_skill':
        if skill:
            stype = _skill_type(skill)
            if stype == 'grammar':
                prompt = GRAMMAR_PRESENT_PROMPT.format(
                    skill_name=skill.name, skill_description=skill.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                initial_phase = 'questions'
            else:
                prompt = VOCAB_PRESENT_PROMPT.format(
                    skill_name=skill.name, skill_description=skill.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                initial_phase = 'guided_practice'
        else:
            prompt = CONVERSATION_PROMPT.format(
                cefr_level=level, interests=interests, last_summary=last_summary
            )
            opening = await call_llm([{"role": "user", "content": prompt}], user=user)
            initial_phase = 'conversation'
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase=initial_phase, phase_turns_completed=0
            )
        )()

    elif session_type == 'srs_review':
        session_skills = await sync_to_async(
            lambda: list(SessionSkill.objects.filter(session=session).select_related('skill').order_by('pk'))
        )()
        due_skills = [{'name': ss.skill.name, 'description': ss.skill.description}
                      for ss in session_skills if ss.skill]
        skill_list = "\n".join(f"- {s['name']}: {s['description']}" for s in due_skills)
        first_skill_name = due_skills[0]['name'] if due_skills else 'the first topic'
        prompt = SRS_REVIEW_PROMPT.format(
            skill_list=skill_list, first_skill_name=first_skill_name, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='review', phase_turns_completed=0
            )
        )()

    elif session_type == 'conversation':
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='conversation', phase_turns_completed=0
            )
        )()

    elif session_type == 'reading':
        prompt = READING_PROMPT.format(
            skill_name=skill.name if skill else 'Spanish',
            skill_description=skill.description if skill else '',
            cefr_level=level, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='present', phase_turns_completed=0
            )
        )()

    elif session_type == 'writing':
        prompt = WRITING_PROMPT.format(
            skill_name=skill.name if skill else 'Spanish',
            skill_description=skill.description if skill else '',
            cefr_level=level, interests=interests,
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                current_phase='prompt', phase_turns_completed=0
            )
        )()

    else:
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )
        opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=opening, user_response='',
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


# ── SRS review phase control ──────────────────────────────────────────────────

async def _continue_srs_review(user, session, text: str) -> dict:
    from learner.models import SessionEvent, SessionSkill, SkillScore

    phase = session.current_phase  # 'review', 'second_pass_check', 'second_pass'
    turns = session.phase_turns_completed

    session_skills = await sync_to_async(
        lambda: list(SessionSkill.objects.filter(session=session).select_related('skill').order_by('pk'))
    )()

    # Build mastery-based question schedule
    skill_ids = [ss.skill.skill_id for ss in session_skills]
    score_map = await sync_to_async(
        lambda: {s.skill.skill_id: s.score
                 for s in SkillScore.objects.filter(user=user, mode='writing').select_related('skill')
                 if s.skill and s.skill.skill_id in skill_ids}
    )()
    schedule = _srs_build_schedule(session_skills, score_map)
    total_questions = sum(q for _, q in schedule)

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:60])
    )()

    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    # Build history
    history = [{"role": "user", "content": "[start]"}]
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    history.append({"role": "user", "content": text})

    # Handle second pass check response
    if phase == 'second_pass_check':
        if text.strip().lower() in SECOND_PASS_PHRASES:
            await _set_phase(session, 'second_pass', 0)
            phase = 'second_pass'
            turns = 0
        else:
            return await _close_session(user, explicit=False)

    # Determine next question position
    next_pos = _srs_position(turns + 1, schedule)

    if next_pos is None:
        # All questions answered — wrap up and offer second pass
        response_text = await call_llm(history, user=user, system_suffix=SRS_REVIEW_COMPLETE_SUFFIX)
        response_text = response_text + "\n\n" + SECOND_PASS_CHECK_STRING
        await _set_phase(session, 'second_pass_check', 0)
    else:
        next_skill, next_q_num, next_q_total = next_pos
        current_pos = _srs_position(turns, schedule)
        current_skill = current_pos[0] if current_pos else None

        if current_skill and current_skill.skill_id == next_skill.skill_id:
            suffix = SRS_REVIEW_SAME_SKILL_SUFFIX.format(
                next_q_num=next_q_num,
                total_q=next_q_total,
                skill_name=next_skill.name,
            )
        else:
            suffix = SRS_REVIEW_NEXT_SKILL_SUFFIX.format(
                next_skill_name=next_skill.name,
                next_skill_description=next_skill.description,
                total_q=next_q_total,
            )

        response_text = await call_llm(history, user=user, system_suffix=suffix)
        await _set_phase(session, phase, turns + 1)

    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='conversation',
        content=response_text,
        user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Conversation phase control ────────────────────────────────────────────────

async def _continue_conversation(user, session, text: str) -> dict:
    from learner.models import SessionEvent, Session
    from .interests import extract_and_store_interests
    from .scoring import score_session

    turns = session.phase_turns_completed

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:30])
    )()

    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    history = [{"role": "user", "content": "[start]"}]
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    history.append({"role": "user", "content": text})

    if turns >= CONVERSATION_TURNS:
        response_text = await call_llm(history, user=user, system_suffix=CONVERSATION_CLOSE_SUFFIX)

        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation',
            content=response_text, user_response='',
        )
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                ended_at=timezone.now(), summary=response_text[:500]
            )
        )()
        await extract_and_store_interests(session, user)
        try:
            await score_session(session, user)
        except Exception:
            pass

        return {"text": response_text, "audio_url": None, "session_ended": True}

    response_text = await call_llm(history, user=user)
    await _set_phase(session, 'conversation', turns + 1)

    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=response_text, user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Reading phase control ─────────────────────────────────────────────────────

async def _continue_reading(user, session, text: str) -> dict:
    from learner.models import SessionEvent, Session
    from .interests import extract_and_store_interests
    from .scoring import score_session

    skill = session.target_skill
    skill_name = skill.name if skill else 'this skill'
    phase = session.current_phase
    turns = session.phase_turns_completed

    if phase == 'complete':
        return await _close_session(user, explicit=False)

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:40])
    )()

    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    history = [{"role": "user", "content": "[start]"}]
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    history.append({"role": "user", "content": text})

    if phase == 'present':
        await _set_phase(session, 'comprehension', 0)
        suffix = READING_COMPREHENSION_SUFFIX.format(q_num=1)
        response_text = await call_llm(history, user=user, system_suffix=suffix)

    elif phase == 'comprehension':
        new_turns = turns + 1
        if new_turns >= READING_PHASE_TURNS['comprehension']:
            await _set_phase(session, 'production', 0)
            suffix = READING_PRODUCTION_SUFFIX.format(skill_name=skill_name, turn_num=1)
        else:
            await _set_phase(session, 'comprehension', new_turns)
            suffix = READING_COMPREHENSION_SUFFIX.format(q_num=new_turns + 1)
        response_text = await call_llm(history, user=user, system_suffix=suffix)

    elif phase == 'production':
        new_turns = turns + 1
        if new_turns >= READING_PHASE_TURNS['production']:
            suffix = READING_CLOSE_SUFFIX.format(skill_name=skill_name)
            response_text = await call_llm(history, user=user, system_suffix=suffix)
            await sync_to_async(SessionEvent.objects.create)(
                session=session, event_type='conversation',
                content=response_text, user_response='',
            )
            await sync_to_async(
                lambda: Session.objects.filter(pk=session.pk).update(
                    ended_at=timezone.now(), summary=response_text[:500]
                )
            )()
            await extract_and_store_interests(session, user)
            try:
                await score_session(session, user)
            except Exception:
                pass
            return {"text": response_text, "audio_url": None, "session_ended": True}
        else:
            await _set_phase(session, 'production', new_turns)
            suffix = READING_PRODUCTION_SUFFIX.format(skill_name=skill_name, turn_num=new_turns + 1)
            response_text = await call_llm(history, user=user, system_suffix=suffix)

    else:
        response_text = await call_llm(history, user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=response_text, user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Writing phase control ─────────────────────────────────────────────────────

async def _continue_writing(user, session, text: str) -> dict:
    from learner.models import SessionEvent, Session
    from .interests import extract_and_store_interests
    from .scoring import score_session

    skill = session.target_skill
    skill_name = skill.name if skill else 'this skill'
    phase = session.current_phase
    turns = session.phase_turns_completed

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:40])
    )()

    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    history = [{"role": "user", "content": "[start]"}]
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    history.append({"role": "user", "content": text})

    async def _do_close():
        suffix = WRITING_CLOSE_SUFFIX.format(skill_name=skill_name)
        response_text = await call_llm(history, user=user, system_suffix=suffix)
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation',
            content=response_text, user_response='',
        )
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                ended_at=timezone.now(), summary=response_text[:500]
            )
        )()
        await extract_and_store_interests(session, user)
        try:
            await score_session(session, user)
        except Exception:
            pass
        return {"text": response_text, "audio_url": None, "session_ended": True}

    if phase == 'prompt':
        await _set_phase(session, 'correction', 0)
        response_text = await call_llm(history, user=user, system_suffix=WRITING_FIRST_ERROR_SUFFIX)

    elif phase == 'correction':
        if turns >= WRITING_CORRECTION_TURNS - 1:
            return await _do_close()
        elif turns % 2 == 0:
            # Student just rewrote an error → confirm + ask for new sentence
            response_text = await call_llm(history, user=user, system_suffix=WRITING_REWRITE_SUFFIX)
            await _set_phase(session, 'correction', turns + 1)
        else:
            # Student wrote new sentence → confirm + present next error
            error_num = (turns // 2) + 2
            suffix = WRITING_NEXT_ERROR_SUFFIX.format(error_num=error_num)
            response_text = await call_llm(history, user=user, system_suffix=suffix)
            await _set_phase(session, 'correction', turns + 1)

    else:
        response_text = await call_llm(history, user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=response_text, user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


# ── Standard session continuation ─────────────────────────────────────────────

async def _continue_session(user, session, text: str, attachments: list = None) -> dict:
    from learner.models import SessionEvent

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:20])
    )()

    # Build history: dummy user message first (API requires user-first),
    # then each event as assistant + the student reply that followed it.
    history = [{"role": "user", "content": "[start]"}]
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})

    history.append({"role": "user", "content": text})

    response_text = await call_llm(history, user=user)

    # Record student response on pending event without touching its content.
    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()

    # Always create a new event for Luz's response.
    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='conversation',
        content=response_text,
        user_response='',
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

    summary_prompt = CLOSE_SUMMARY_PROMPT_ES if _is_advanced(user) else CLOSE_SUMMARY_PROMPT_EN
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
