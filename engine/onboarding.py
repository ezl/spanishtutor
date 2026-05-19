"""
Onboarding: new user → 10-15 question adaptive placement quiz → skill grid + CEFR estimate.

Quiz has two phases:
  Q1-5:  Binary search for CEFR level (multiple choice, starts A1)
  Q6-15: Boundary probing — find where confident→developing on specific subskills

Two strands woven together: grammar and vocabulary.
"""
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm

FIRST_MESSAGE = """Hola! Soy Luz Angela, tu profesora de español!
Hi! I'm Luz Angela, your Spanish teacher!

I'll ask you 10-15 quick questions to figure out your level. If something stumps you, just say "I don't know" — that's more useful than a guess.

*(Send `!reset` anytime to start over.)*"""

# Sentinel used by bot/client.py to fire Q1 immediately on join
QUIZ_START_SENTINEL = "\x00__quiz_start__\x00"

QUIZ_PROMPT = """You are Luz Angela running an adaptive Spanish placement quiz.

RULES:
- Never penalize missing accents (ó, é, etc) — typing limitation, not a knowledge gap
- "I don't know" / "no idea" / "not sure" / "idk" = unanswered, mark unknown, move on without judgment
- Never reveal what skill you're measuring mid-quiz
- Escalate question format as level becomes clear:
    A1-A2: multiple choice (label options a/b/c/d)
    A2-B1: fill-in-the-blank (show the sentence, no options)
    B1-B2: short free production ("how do you say X?" or "translate this")
    B2+:   natural Spanish conversation ("¿Qué hiciste ayer?", describe plans, etc.)

TWO STRANDS — weave both through the quiz:
  Grammar:    ser_estar, preterite, preterite_vs_imperfect, reflexive_verbs, conditional_subjunctive
  Vocabulary: vocab_concrete_nouns, vocab_body_parts, vocab_descriptive, vocab_register

QUIZ HISTORY ({quiz_count} questions answered so far):
{history}

LATEST INPUT: {latest}

PHASE INSTRUCTIONS:
{phase}

SKILL GRID — score each skill based on all evidence so far:
  unknown | shaky | developing | confident | mastered

OUTPUT FORMAT — use exactly one of these two blocks:

If continuing:
CONTINUE
SKILL_UPDATES: skill:score,skill:score  (or "none" if no new info)
NEXT_QUESTION: <full question text, including options if multiple choice>

If concluding (10+ questions answered, OR level and key gaps are clearly established):
CONCLUDE
CEFR_LEVEL: A1|A2|B1|B2|C1|C2
SKILL_UPDATES: skill:score for every skill you have signal on
ASSESSMENT: <share with student — their level, what they know well, specific gaps, what first sessions will focus on. Warm and direct. 4-6 sentences in English.>"""

PHASE_BINARY_SEARCH = "Q1-5: Binary search for CEFR. Start A1. Harder if correct, easier if wrong."
PHASE_BOUNDARY_PROBE = "Q6+: Working CEFR estimate established. Now probe subskill boundaries. Find where confident→developing. Alternate grammar and vocabulary strands. Target specific gaps."
PHASE_FIRST_QUESTION = "This is Q1. Ignore the input — ask the very first question. Start A1 (e.g. 'what does \"gracias\" mean?'). Use multiple choice."


def _parse_quiz_response(text: str) -> dict:
    """Parse LLM quiz response into a dict. Handles multi-line NEXT_QUESTION/ASSESSMENT."""
    result = {}
    lines = text.strip().split('\n')
    result['action'] = lines[0].strip()

    current_key = None
    current_lines = []
    keys = {'SKILL_UPDATES', 'NEXT_QUESTION', 'CEFR_LEVEL', 'ASSESSMENT'}

    for line in lines[1:]:
        matched_key = None
        for key in keys:
            if line.startswith(key + ':'):
                matched_key = key
                break
        if matched_key:
            if current_key:
                result[current_key] = '\n'.join(current_lines).strip()
            current_key = matched_key
            current_lines = [line[len(matched_key) + 1:].strip()]
        elif current_key:
            current_lines.append(line)

    if current_key:
        result[current_key] = '\n'.join(current_lines).strip()

    return result


async def _save_skill_updates(user, updates_str: str):
    """Parse and save SKILL_UPDATES string to SkillScore rows."""
    if not updates_str or updates_str.lower() == 'none':
        return
    from learner.models import SkillScore
    score_map = {'shaky': 1, 'developing': 2, 'confident': 3, 'mastered': 4}
    for pair in updates_str.split(','):
        pair = pair.strip()
        if ':' not in pair:
            continue
        skill_id, _, score_str = pair.partition(':')
        score = score_map.get(score_str.strip())
        if score:
            await sync_to_async(SkillScore.objects.update_or_create)(
                user=user,
                skill_id=skill_id.strip(),
                mode='writing',
                defaults={'score': score},
            )


async def handle_onboarding(user, text: str, attachments: list = None) -> dict:
    return await _step_adaptive_quiz(user, text)


async def _step_adaptive_quiz(user, text: str) -> dict:
    from learner.models import Session, SessionEvent, EvaluationProgress

    is_first = text == QUIZ_START_SENTINEL

    # Get or create quiz session
    session = await sync_to_async(
        lambda: Session.objects.filter(
            user=user, session_type='onboarding', ended_at__isnull=True
        ).first()
    )()
    if not session:
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding'
        )

    events = await sync_to_async(
        lambda: list(session.events.filter(event_type='quiz').order_by('timestamp'))
    )()
    quiz_count = len(events)

    # Log user's answer onto the last unanswered event
    if not is_first and events:
        last = events[-1]
        if not last.user_response:
            await sync_to_async(
                lambda: SessionEvent.objects.filter(pk=last.pk).update(user_response=text)
            )()
            # Refresh local copy
            last.user_response = text

    history_text = '\n'.join(
        f"Q{i+1}: {e.content}\nA: {e.user_response or '(unanswered)'}"
        for i, e in enumerate(events)
    ) or '(none yet)'

    if is_first:
        phase = PHASE_FIRST_QUESTION
        latest = '(quiz start)'
    elif quiz_count < 5:
        phase = PHASE_BINARY_SEARCH
        latest = f'"{text}"'
    else:
        phase = PHASE_BOUNDARY_PROBE
        latest = f'"{text}"'

    prompt = QUIZ_PROMPT.format(
        quiz_count=quiz_count,
        history=history_text,
        latest=latest,
        phase=phase,
    )

    llm_response = await call_llm(
        [{"role": "user", "content": prompt}],
        user=None,
        max_tokens=600,
    )

    parsed = _parse_quiz_response(llm_response)
    await _save_skill_updates(user, parsed.get('SKILL_UPDATES', ''))

    if parsed['action'] == 'CONCLUDE':
        cefr = parsed.get('CEFR_LEVEL', 'A2')
        assessment = parsed.get('ASSESSMENT', f"You're at {cefr} level. Let's get started!")

        await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(
            estimated_cefr_level=cefr,
            onboarding_complete=True,
        )
        await sync_to_async(EvaluationProgress.objects.get_or_create)(
            user=user, phase='session1'
        )
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(ended_at=timezone.now())
        )()

        return {"text": assessment, "audio_url": None, "session_ended": False}

    # CONTINUE — log next question and return it
    next_q = parsed.get('NEXT_QUESTION', '')
    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='quiz',
        dimension='writing',
        content=next_q,
        user_response='',
    )
    return {"text": next_q, "audio_url": None, "session_ended": False}
