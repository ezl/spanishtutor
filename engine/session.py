"""
Post-onboarding session handling: open, content loop, close.
"""
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm
from .curriculum import LEVEL_ORDER, get_skill, next_new_skill, next_new_vocab_skill

GOODBYE_WORDS = {'bye', 'adiós', 'adios', 'chao', 'hasta luego', 'goodbye', 'ciao', 'nos vemos'}

# ── Session prompt templates ───────────────────────────────────────────────────

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

NEW_SKILL_PROMPT = """The student is starting a lesson on a new skill.

Target skill: {skill_name}
What it covers: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Session instructions:
- One sentence to open — what they're working on today. No long intro.
- Give ONE clear example using their interests that demonstrates the skill.
- Immediately prompt production: "Your turn —" with a specific drill prompt.
- Run 3-4 drill exchanges.
- Close with what they did well and that this skill will come back for review.

Be direct and brief. Generate the opening and first example now."""

VOCAB_SPRINT_PROMPT = """The student is starting a vocabulary session.

Target vocabulary: {skill_name}
What it covers: {skill_description}
Student level: {cefr_level}
Student interests: {interests}

Session instructions:
- Introduce 5-7 words. Each gets one example sentence using the student's interests.
- Present all words first, then quiz immediately: alternate EN->ES and ES->EN.
- End by asking them to use two of the words in a sentence about their own life.
- Close: "These will come back for review in a few days."

Generate the opening message introducing the vocabulary batch now."""

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


# ── Decision tree ──────────────────────────────────────────────────────────────

async def _select_session(user):
    """
    Returns (session_type: str, context: dict).
    context keys vary by type — passed straight to the prompt template.
    """
    from learner.models import SkillScore, Session

    now = timezone.now()
    level = user.estimated_cefr_level or 'A1'
    level_idx = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 0

    scores = await sync_to_async(
        lambda: list(SkillScore.objects.filter(user=user))
    )()
    scored_ids = {s.skill_id for s in scores}

    # 1. SRS Review — highest priority if 3+ skills are overdue
    due = [s for s in scores if s.score > 0 and s.next_review_at and s.next_review_at <= now]
    due.sort(key=lambda s: s.next_review_at)
    if len(due) >= 3:
        due_skills = [get_skill(s.skill_id) for s in due[:6]]
        due_skills = [s for s in due_skills if s]
        return 'srs_review', {'due_skills': due_skills}

    # 2. Vocab sprint — vocab significantly lagging grammar
    vocab_scores = [s for s in scores if 'vocab' in s.skill_id]
    grammar_scores = [s for s in scores if 'vocab' not in s.skill_id]
    if vocab_scores and grammar_scores:
        vocab_avg = sum(s.score for s in vocab_scores) / len(vocab_scores)
        grammar_avg = sum(s.score for s in grammar_scores) / len(grammar_scores)
        if vocab_avg < grammar_avg - 1.0:
            vocab_skill = next_new_vocab_skill(level, scored_ids)
            if vocab_skill:
                return 'vocab_sprint', {'skill': vocab_skill}

    # 3. Conversation — B1+ and not in last 3 sessions
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
            return 'conversation', {}

    # 4. Reading — B1+ and reading mode significantly underscored
    if level_idx >= 2:
        reading = [s for s in scores if s.mode == 'reading']
        other = [s for s in scores if s.mode != 'reading']
        if reading and other:
            if (sum(s.score for s in reading) / len(reading)) < (sum(s.score for s in other) / len(other)) - 1.0:
                return 'reading', {}

    # 5. Default: push a new skill
    skill = next_new_skill(level, scored_ids)
    return 'new_skill', {'skill': skill}


# ── Session lifecycle ──────────────────────────────────────────────────────────

def _is_goodbye(text: str) -> bool:
    return text.strip().lower() in GOODBYE_WORDS


async def handle_session(user, text: str, attachments: list = None) -> dict:
    from learner.models import Session

    if _is_goodbye(text):
        return await _close_session(user, explicit=True)

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .first()
    )()

    if not session:
        return await _open_session(user, text)

    return await _continue_session(user, session, text, attachments)


async def _open_session(user, text: str) -> dict:
    from learner.models import Session, SessionEvent

    session_type, context = await _select_session(user)

    last_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=False)
                               .exclude(session_type='onboarding')
                               .order_by('-ended_at').first()
    )()
    last_summary = last_session.summary[:120] if last_session and last_session.summary else "this is their first session"

    session = await sync_to_async(Session.objects.create)(
        user=user, session_type=session_type
    )

    level = user.estimated_cefr_level or 'A1'
    interests = user.interests or "not yet known"

    if session_type == 'srs_review':
        skill_list = "\n".join(
            f"- {s['name']}: {s['description']}" for s in context['due_skills']
        )
        prompt = SRS_REVIEW_PROMPT.format(skill_list=skill_list, interests=interests)

    elif session_type == 'new_skill':
        skill = context.get('skill')
        if not skill:
            prompt = CONVERSATION_PROMPT.format(
                cefr_level=level, interests=interests, last_summary=last_summary
            )
        else:
            prompt = NEW_SKILL_PROMPT.format(
                skill_name=skill['name'],
                skill_description=skill['description'],
                cefr_level=level,
                interests=interests,
            )

    elif session_type == 'vocab_sprint':
        skill = context['skill']
        prompt = VOCAB_SPRINT_PROMPT.format(
            skill_name=skill['name'],
            skill_description=skill['description'],
            cefr_level=level,
            interests=interests,
        )

    elif session_type == 'conversation':
        prompt = CONVERSATION_PROMPT.format(
            cefr_level=level, interests=interests, last_summary=last_summary
        )

    elif session_type == 'reading':
        prompt = READING_PROMPT.format(cefr_level=level, interests=interests)

    elif session_type == 'writing':
        prompt = WRITING_PROMPT.format(cefr_level=level, interests=interests)

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


async def _close_session(user, explicit: bool = True) -> dict:
    import django.conf
    from learner.models import Session
    from .interests import extract_and_store_interests

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

    if session:
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                ended_at=timezone.now(),
                summary=summary[:500],
            )
        )()

        facts = await extract_and_store_interests(session, user)

        dev_log = None
        if django.conf.settings.DEV_MODE:
            if facts:
                lines = [
                    f"  {f['topic']} ({f['category']}, {f['confidence']}) {'[new]' if f.get('new') else '[reinforced]'}"
                    for f in facts
                ]
                dev_log = "[dev] interests extracted:\n" + "\n".join(lines)
            else:
                dev_log = "[dev] no interests extracted this session"
    else:
        dev_log = None

    return {"text": summary, "audio_url": None, "session_ended": True, "dev_log": dev_log}
