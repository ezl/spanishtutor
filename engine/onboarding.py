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

FIRST_MESSAGE = """👋 Hola! Soy Luz Angela, tu profesora de español!
(Hi! I'm Luz Angela, your Spanish teacher!)

I'm excited to start our Spanish language journey. What's your name?"""

LISTO_WORDS = {'listo', 'lista', 'ready', 'vamos', 'ok', 'okay', 'yes', 'si', 'sí', 'go', "let's go", 'dale'}

# Sentinel used by bot/client.py to fire Q1 immediately on join
QUIZ_START_SENTINEL = "\x00__quiz_start__\x00"

QUIZ_PROMPT = """You are running a Spanish placement evaluation. Your ONLY job is to assess level — not teach, not coach, not encourage.

STRICT RULES:
- ONE attempt per question. Never re-ask, never hint, never say "almost" or "close".
- Give NO feedback on individual answers. Just move to the next question.
- Never reveal what skill you are measuring.
- Missing accents (esta→está, el→él) = treat as correct. Lazy typing, not a knowledge gap.
- "I don't know" / "no idea" / "idk" = unanswered, mark unknown, move on silently.
- Do NOT say "good", "nice", "great", or any praise. Pure neutral transitions only.

QUESTION FORMAT by level:
  A1-A2: multiple choice, label options a/b/c/d
  A2-B1: fill-in-the-blank, write blank as ________ (8 underscores). The blank replaces ONLY the missing word/verb — never wrap extra words in the blank. e.g. "Ella ________ cansada hoy." not "Ella ________ cansada hoy________ verano."
  B1-B2: free production — "How do you say X?" or "Translate: ..."
  B2+:   natural Spanish — "¿Qué hiciste ayer?" etc., student answers in Spanish

TWO STRANDS — weave both through the quiz, using these exact skill IDs:

  Grammar strand:
    a1_ser_estar_basic, a1_present_regular, a2_present_irregular, a2_reflexive_verbs,
    a2_object_pronouns, a2_preterite_regular, b1_preterite_irregular, b1_imperfect,
    b1_preterite_vs_imperfect, b1_future, b1_conditional, b1_subjunctive_intro,
    b1_ser_estar_advanced, b2_subjunctive_present, b2_subjunctive_past,
    b2_passive_voice, b2_advanced_pronouns, b2_reported_speech, b2_discourse_markers,
    c1_subjunctive_all, c1_compound_tenses, c1_register_shifts

  Vocabulary strand (test with concrete words — translate, identify, produce):
    a1_basic_vocab       → colors, body parts, food, family, basic objects
    a1_greetings         → common expressions
    a2_vocab_domains     → travel, shopping, directions, health, weather
    a2_gustar_like_verbs → gustar/encantar/molestar constructions
    b1_vocab_intermediate → work, opinions, emotions, media, technology
    b2_vocab_advanced    → abstract concepts, formal register, collocations
    c1_idiomatic_expressions → fixed phrases, collocations
    c1_vocab_academic    → academic/professional vocabulary

QUIZ HISTORY ({quiz_count} questions so far):
{history}

LATEST INPUT: {latest}

PHASE: {phase}

SKILL GRID — update scores based on all evidence:
  unknown | shaky | developing | confident | mastered

OUTPUT FORMAT:

If continuing:
CONTINUE
SKILL_UPDATES: skill:score,skill:score  (or "none")
NEXT_QUESTION: <question only — no preamble, no praise, no transition commentary>

CONCLUDE only if ALL of these are true:
- 10+ questions answered
- At least 3 vocabulary questions asked covering skills from the vocabulary strand (a1_basic_vocab, a2_vocab_domains, b1_vocab_intermediate, etc.)
- At least 2 boundary-probe grammar questions asked
- CEFR level and key gaps are clearly established
Otherwise always CONTINUE.
CONCLUDE
CEFR_LEVEL: A1|A2|B1|B2|C1|C2
SKILL_UPDATES: skill:score for every assessed skill
STRONG: <comma-separated list of strong skills, plain English, e.g. "Preterite tense, Ser vs. estar">
WEAK: <comma-separated list of weak/shaky skills>
FOCUS: <1 sentence on what first sessions will target>"""

PHASE_BINARY_SEARCH = """Q1-5: Binary search for CEFR estimate. Start A1. Go harder if correct, easier if wrong.
The binary search implicitly confirms the floor — if you see 2+ correct answers at level-1, treat that level as mastered and don't re-probe it unless answers are inconsistent."""

PHASE_BOUNDARY_PROBE = """Q6+: You have a working CEFR estimate. Now do structured boundary probing:

(B) AT estimated level — at least 2 questions targeting specific skills at this level to find internal variation (which skills are solid vs shaky).
(C) ABOVE estimated level — at least 2 questions at the next level up to find where they break.
(A) BELOW estimated level — only probe if the student seems inconsistent (wrong on easy things, right on hard ones). Otherwise skip — binary search already confirmed the floor.

Alternate grammar and vocabulary strands. Target the specific skill IDs from the taxonomy."""

PHASE_FIRST_QUESTION = "This is Q1. Ignore the input — ask the very first question. Start A1 (e.g. 'what does \"gracias\" mean?'). Use multiple choice."


def _parse_quiz_response(text: str) -> dict:
    """Parse LLM quiz response into a dict. Handles multi-line NEXT_QUESTION/ASSESSMENT."""
    result = {}
    lines = text.strip().split('\n')
    result['action'] = lines[0].strip()

    current_key = None
    current_lines = []
    keys = {'SKILL_UPDATES', 'NEXT_QUESTION', 'CEFR_LEVEL', 'ASSESSMENT', 'STRONG', 'WEAK', 'FOCUS'}

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
    if not user.display_name:
        return await _step_collect_name(user, text)

    # Check if quiz has started yet
    has_quiz_events = await sync_to_async(
        lambda: user.sessions.filter(session_type='onboarding')
                              .filter(events__event_type='quiz')
                              .exists()
    )()
    if not has_quiz_events:
        return await _step_listo_gate(user, text)

    return await _step_adaptive_quiz(user, text)


async def _step_collect_name(user, text: str) -> dict:
    # Extract just the name in case they wrote "I'm Eric" or "My name is Eric"
    name = text.strip().split()[-1].capitalize() if text.strip() else text.strip()
    # Save as display_name
    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(display_name=name)

    welcome = (
        f"Welcome, {name}! I'm excited to begin our Spanish journey together.\n\n"
        f"👉 I'm going to ask you a few questions to get a sense of your current level.\n"
        f"👉 If you aren't sure, just say \"I don't know\"\n\n"
        f"When you're ready to get started, say **listo** (that means \"ready\" in Spanish 😊)"
    )
    return {"text": welcome, "audio_url": None, "session_ended": False}


async def _step_listo_gate(user, text: str) -> dict:
    if text.strip().lower() in LISTO_WORDS:
        return await _step_adaptive_quiz(user, QUIZ_START_SENTINEL)
    return {"text": "Say **listo** when you're ready to start!", "audio_url": None, "session_ended": False}


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

        strong_items = [f"— {s.strip()}" for s in parsed.get('STRONG', '').split(',') if s.strip()]
        weak_items = [f"— {s.strip()}" for s in parsed.get('WEAK', '').split(',') if s.strip()]
        focus = parsed.get('FOCUS', '').strip()

        assessment_parts = [f"📊 **Your Spanish level: {cefr}**\n"]
        if strong_items:
            assessment_parts.append("✅ **Strong:**\n" + "\n".join(strong_items))
        if weak_items:
            assessment_parts.append("⚠️ **Needs work:**\n" + "\n".join(weak_items))
        if focus:
            assessment_parts.append(f"🎯 **First sessions will focus on:** {focus}")
        assessment = "\n\n".join(assessment_parts)

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
