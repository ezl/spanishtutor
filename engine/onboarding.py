"""
Onboarding: new user → adaptive placement quiz → skill grid + CEFR estimate.

Quiz phases:
  Q1-5:  Binary search for CEFR level
  Q6+:   Boundary probing at estimated level
"""
import logging
import re
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm

log = logging.getLogger('onboarding')


def _fix_blanks(text: str) -> str:
    """Replace any LLM blank variants with the canonical 8-underscore format."""
    return re.sub(r'\(\s*_+\s*\)', '________', text)


# ---------------------------------------------------------------------------
# Hardcoded strings — never LLM-generated
# ---------------------------------------------------------------------------

FIRST_MESSAGE = """👋 Hola! Soy Luz Angela, tu profesora de español!
(Hi! I'm Luz Angela, your Spanish teacher!)

I'm excited to start our Spanish language journey. What's your name?"""

LISTO_WORDS = {'listo', 'lista', 'ready', 'vamos', 'ok', 'okay', 'yes', 'si', 'sí', 'go', "let's go", 'dale'}

QUIZ_START_SENTINEL = "\x00__quiz_start__\x00"

DONT_KNOW_PHRASES = {
    "i don't know", "i dont know", "idk", "no se", "no sé",
    "dunno", "not sure", "no idea", "i have no idea", "no clue",
}

RAGE_WORDS = {
    'wtf', 'stop', 'quit', 'fuck', 'shit', 'stupid', 'hate',
    'horrible', 'terrible', 'awful', 'boring', 'dumb', 'useless',
    'broken', 'worst',
}

REDIRECT_MESSAGE = (
    "I'm just here to assess your Spanish right now — "
    "I'll be a better conversation partner once we're done! "
    "Let's continue:\n\n{question}"
)

GUESSING_REMINDER = (
    "Quick reminder — if you're not sure, just say *I don't know* or *no sé*. "
    "No penalty, it actually helps me place you better.\n\n"
)

MENU_MESSAGE = """No worries! Here are your options:

**1.** Start over — reset the quiz from the beginning
**2.** English mode — I'll give instructions in English from here
**3.** How does this work? — explain what I'm doing and why

Reply with 1, 2, or 3."""

HOW_IT_WORKS = (
    "I'm giving you a short placement quiz to figure out your Spanish level — "
    "somewhere between A1 (beginner) and C2 (fluent). I'll ask a few questions, "
    "starting easy and adjusting based on your answers — it should take about 2–3 minutes. "
    "Once I have a clear picture, we'll start actual lessons tailored to exactly where you are. "
    "Just answer as best you can — if you don't know, say *no sé*."
)

POST_ASSESSMENT_EN = (
    'Listo! Now the real fun starts. Any time you want to practice, just say something like '
    '*"let\'s do a lesson"* or *"teach me something"* — I\'ll take it from there.\n\n'
    "If you're ready for a lesson, try it now by asking me to start a lesson!"
)

POST_ASSESSMENT_ES = (
    '¡Listo! Ahora empieza la diversión. Cuando quieras practicar, solo dime algo como '
    '*"hagamos una lección"* o *"enséñame algo"* — yo me encargo.\n\n'
    '¡Si estás listo para una lección, pruébalo ahora y pídeme que empecemos!'
)

# ---------------------------------------------------------------------------
# Quiz prompt
# ---------------------------------------------------------------------------

QUIZ_PROMPT = """You are running a Spanish placement evaluation. Your ONLY job is to assess level — not teach, not coach, not encourage.

STRICT RULES:
- ONE attempt per question. Never re-ask, never hint, never say "almost" or "close".
- Give NO feedback on individual answers. Just move to the next question.
- Never reveal what skill you are measuring.
- Missing accents (esta→está, el→él) = treat as correct. Lazy typing, not a knowledge gap.
- "I don't know" / "no idea" / "idk" = unanswered, mark unknown, move on silently.
- Do NOT say "good", "nice", "great", or any praise. Pure neutral transitions only.
- Never embed the target word, grammatical form, or structure in the question text. No scaffolding hints ever.
- If the student fails or says "I don't know" on the same skill at the same level twice, record the score and move on immediately.
- If the student sends something unrelated to the quiz, ignore it and ask the next question as if it wasn't sent.
- If the last 3 answers all resulted in shaky or unknown skill scores (consecutive wrong answers), prepend this exact text to your NEXT_QUESTION: "Quick reminder — if you're not sure, just say *I don't know* or *no sé*. No penalty, it actually helps me place you better.\n\n"

QUESTION FORMAT by level:
  A1-A2: multiple choice, label options a/b/c/d. Always end with this exact line on its own:
    *(Not sure? Say "I don't know" — it's more useful than guessing)*
  A2-B1: fill-in-the-blank. Format exactly like this, two lines:
    "Ella ________ cansada hoy."
    *(Translation: She is tired today.)*
    The translation is always complete — it reveals the answer in English. Never put a blank in the translation line.
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

{english_mode_note}

SKILL GRID — update scores based on all evidence:
  unknown | shaky | developing | confident | mastered

OUTPUT FORMAT:

If continuing:
CONTINUE
SKILL_UPDATES: skill:score,skill:score  (or "none")
NEXT_QUESTION: <question only — no preamble, no praise, no transition commentary>

CONCLUDE when ALL of these are true:
- CEFR level is clearly established
- At least 3 consecutive skills in the taxonomy have scores (consecutive = adjacent on the A1→C2 ladder, e.g. the last A2 skill and first two B1 skills). This ensures depth at the boundary, not scattered data points.
- The boundary between what the student knows and doesn't know is located (you've seen both correct and incorrect, or consistent failure at a level)

For true beginners showing consistent failure at A1, this may happen in 5-6 questions. For students near a B1/B2 boundary, it may take 12+. Conclude as soon as confidence is high — do not pad with extra questions. Only CONTINUE if there is a specific named ambiguity that exactly one more question would resolve.

CONCLUDE
CEFR_LEVEL: A1|A2|B1|B2|C1|C2
SKILL_UPDATES: skill:score for every assessed skill
COMMENT: <1-2 sentences, first person, directed to the student. Something specific you genuinely noticed — write "your..." or "you...", never "the student...". Warm but honest. Example: "Your instinct on individual words is solid — full sentences are where things get fuzzy, and that's exactly what we'll work on.">"""

PHASE_BINARY_SEARCH = """Q1-5: Binary search for CEFR estimate. Start A1. Go harder if correct, easier if wrong.
The binary search implicitly confirms the floor — if you see 2+ correct answers at level-1, treat that level as mastered and don't re-probe it unless answers are inconsistent."""

PHASE_BOUNDARY_PROBE = """Q6+: You have a working CEFR estimate. Now do structured boundary probing:

(B) AT estimated level — at least 2 questions targeting specific skills at this level to find internal variation (which skills are solid vs shaky).
(C) ABOVE estimated level — at least 2 questions at the next level up to find where they break.
(A) BELOW estimated level — only probe if the student seems inconsistent (wrong on easy things, right on hard ones). Otherwise skip — binary search already confirmed the floor.

Alternate grammar and vocabulary strands. Target the specific skill IDs from the taxonomy."""

PHASE_FIRST_QUESTION = "This is Q1. Ignore the input — ask the very first question. Start A1 (e.g. 'what does \"gracias\" mean?'). Use multiple choice."

# ---------------------------------------------------------------------------
# Input classification
# ---------------------------------------------------------------------------

def _classify_input(text: str) -> str:
    """Classify user input: 'answer' | 'dont_know' | 'off_topic' | 'rage'"""
    t = text.strip().lower()
    if t in DONT_KNOW_PHRASES:
        return 'dont_know'
    words = set(t.split())
    if words & RAGE_WORDS:
        return 'rage'
    if text.strip().endswith('?') and len(text.strip()) > 15:
        return 'off_topic'
    return 'answer'

# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_quiz_response(text: str) -> dict:
    """Parse LLM quiz response into a dict. Handles multi-line values."""
    result = {}
    lines = text.strip().split('\n')

    # Scan all lines for the action word — LLM sometimes adds preamble before it
    action = 'CONTINUE'
    action_line_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ('CONTINUE', 'CONCLUDE'):
            action = stripped
            action_line_idx = i
            break
    result['action'] = action

    current_key = None
    current_lines = []
    keys = {'SKILL_UPDATES', 'NEXT_QUESTION', 'CEFR_LEVEL', 'COMMENT'}

    for line in lines[action_line_idx + 1:]:
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

# ---------------------------------------------------------------------------
# Skill score persistence
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

async def _show_menu(session, uid: str) -> dict:
    from learner.models import SessionEvent
    log.info('[%s] quiz: showing menu', uid)
    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='menu',
        content=MENU_MESSAGE,
        user_response='',
    )
    return {"text": MENU_MESSAGE, "audio_url": None, "session_ended": False}


async def _handle_menu_response(user, session, text: str, menu_event, quiz_events: list) -> dict:
    from learner.models import SessionEvent, User
    uid = user.discord_id
    t = text.strip().lower()

    await sync_to_async(
        lambda: SessionEvent.objects.filter(pk=menu_event.pk).update(user_response=text)
    )()
    log.info('[%s] menu: response %r', uid, t)

    if t in ('1', 'start over', 'restart', 'reset'):
        log.info('[%s] menu: start over', uid)
        discord_id = user.discord_id
        await sync_to_async(User.objects.filter(discord_id=discord_id).delete)()
        await sync_to_async(User.objects.create)(discord_id=discord_id, display_name='')
        return {"text": FIRST_MESSAGE, "audio_url": None, "session_ended": False}

    elif t in ('2', 'english', 'english mode'):
        log.info('[%s] menu: english mode selected', uid)
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='meta', content='english_mode', user_response='set'
        )
        last_q = next((e for e in reversed(quiz_events) if e.content), None)
        reask = f"\n\nLet's continue:\n\n{last_q.content}" if last_q else ""
        return {"text": f"Got it — I'll give instructions in English from here.{reask}", "audio_url": None, "session_ended": False}

    elif t in ('3', 'how', 'how does this work', 'explain', 'what is this'):
        log.info('[%s] menu: how it works selected', uid)
        last_q = next((e for e in reversed(quiz_events) if e.content), None)
        reask = f"\n\nLet's continue:\n\n{last_q.content}" if last_q else ""
        return {"text": HOW_IT_WORKS + reask, "audio_url": None, "session_ended": False}

    else:
        log.info('[%s] menu: unrecognized response %r, re-showing', uid, t)
        return {"text": "Please reply with **1**, **2**, or **3**.\n\n" + MENU_MESSAGE, "audio_url": None, "session_ended": False}

# ---------------------------------------------------------------------------
# Onboarding router
# ---------------------------------------------------------------------------

async def handle_onboarding(user, text: str, attachments: list = None) -> dict:
    uid = user.discord_id
    if not user.display_name:
        log.info('[%s] onboarding: collecting name', uid)
        return await _step_collect_name(user, text)

    has_quiz_events = await sync_to_async(
        lambda: user.sessions.filter(session_type='onboarding')
                              .filter(events__event_type='quiz')
                              .exists()
    )()
    if not has_quiz_events:
        log.info('[%s] onboarding: listo gate — input=%r', uid, text)
        return await _step_listo_gate(user, text)

    return await _step_adaptive_quiz(user, text)


async def _step_collect_name(user, text: str) -> dict:
    name = text.strip().split()[-1].capitalize() if text.strip() else text.strip()
    log.info('[%s] onboarding: name collected — %r', user.discord_id, name)
    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(display_name=name)
    welcome = (
        f"Welcome, {name}!\n\n"
        f"👉 I'm going to ask you a few questions to get a sense of your current level.\n"
        f"👉 If you aren't sure, just say \"I don't know\"\n\n"
        f"When you're ready to get started, say **listo** (that means \"ready\" in Spanish 😊)"
    )
    return {"text": welcome, "audio_url": None, "session_ended": False}


async def _step_listo_gate(user, text: str) -> dict:
    if text.strip().lower() in LISTO_WORDS:
        log.info('[%s] quiz: starting — listo received', user.discord_id)
        return await _step_adaptive_quiz(user, QUIZ_START_SENTINEL)
    return {"text": "Say **listo** when you're ready to start!", "audio_url": None, "session_ended": False}

# ---------------------------------------------------------------------------
# Adaptive quiz
# ---------------------------------------------------------------------------

async def _step_adaptive_quiz(user, text: str) -> dict:
    from learner.models import Session, SessionEvent, EvaluationProgress

    uid = user.discord_id
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
        log.info('[%s] quiz: session created — id=%s', uid, session.pk)

    # Load all events so we can inspect meta/menu/redirect state
    all_events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp'))
    )()
    quiz_events = [e for e in all_events if e.event_type == 'quiz']
    quiz_count = len(quiz_events)

    # --- Menu response state ---
    last_event = all_events[-1] if all_events else None
    if last_event and last_event.event_type == 'menu' and not last_event.user_response:
        return await _handle_menu_response(user, session, text, last_event, quiz_events)

    prepend_reminder = False

    if not is_first:
        input_class = _classify_input(text)
        log.info('[%s] quiz: input classified=%r text=%r', uid, input_class, text[:60])

        # Rage → menu immediately
        if input_class == 'rage':
            log.info('[%s] quiz: rage → menu', uid)
            return await _show_menu(session, uid)

        # Off-topic handling
        if input_class == 'off_topic':
            last_quiz_q = quiz_events[-1].content if quiz_events else ""
            # Detect consecutive off-topic: was there a redirect with no quiz answer after it?
            last_redirect = next((e for e in reversed(all_events) if e.event_type == 'redirect'), None)
            if last_redirect:
                redirect_idx = all_events.index(last_redirect)
                quiz_after = any(
                    e.event_type == 'quiz' and all_events.index(e) > redirect_idx
                    for e in quiz_events
                )
                consecutive = not quiz_after
            else:
                consecutive = False

            if consecutive:
                log.info('[%s] quiz: off-topic x2 → menu', uid)
                return await _show_menu(session, uid)

            log.info('[%s] quiz: off-topic → redirect', uid)
            redirect_text = REDIRECT_MESSAGE.format(question=last_quiz_q)
            await sync_to_async(SessionEvent.objects.create)(
                session=session, event_type='redirect',
                content=redirect_text, user_response=text,
            )
            return {"text": redirect_text, "audio_url": None, "session_ended": False}

        # Record answer on last unanswered quiz event
        if quiz_events and not quiz_events[-1].user_response:
            log.info('[%s] quiz: turn %d answer — %r', uid, quiz_count, text[:60])
            await sync_to_async(
                lambda: SessionEvent.objects.filter(pk=quiz_events[-1].pk).update(user_response=text)
            )()
            quiz_events[-1].user_response = text
        else:
            log.warning('[%s] quiz: turn %d — no unanswered event to record response', uid, quiz_count)

        # Guessing reminder: 3+ shaky skill scores = consecutive wrong answers, not yet shown
        reminder_shown = any(e.event_type == 'meta' and e.content == 'guessing_reminder_shown' for e in all_events)
        if not reminder_shown:
            from learner.models import SkillScore
            shaky_count = await sync_to_async(
                lambda: SkillScore.objects.filter(user=user, score=1).count()
            )()
            if shaky_count >= 3:
                log.info('[%s] quiz: guessing reminder triggered (%d shaky scores)', uid, shaky_count)
                await sync_to_async(SessionEvent.objects.create)(
                    session=session, event_type='meta',
                    content='guessing_reminder_shown', user_response='set',
                )
                prepend_reminder = True

    # Build history from quiz events only
    history_text = '\n'.join(
        f"Q{i+1}: {e.content}\nA: {e.user_response or '(unanswered)'}"
        for i, e in enumerate(quiz_events)
    ) or '(none yet)'

    # Determine phase
    if is_first:
        phase = PHASE_FIRST_QUESTION
        latest = '(quiz start)'
        log.info('[%s] quiz: Q1 — binary search phase', uid)
    elif quiz_count < 5:
        phase = PHASE_BINARY_SEARCH
        latest = f'"{text}"'
        log.info('[%s] quiz: Q%d — binary search', uid, quiz_count + 1)
    else:
        phase = PHASE_BOUNDARY_PROBE
        latest = f'"{text}"'
        if quiz_count == 5:
            log.info('[%s] quiz: Q%d — entering boundary probe', uid, quiz_count + 1)
        else:
            log.info('[%s] quiz: Q%d — boundary probe', uid, quiz_count + 1)

    english_mode = any(e.event_type == 'meta' and e.content == 'english_mode' for e in all_events)
    english_mode_note = (
        "ENGLISH MODE: All question instructions must be written in English. "
        "Spanish answers are still expected where relevant."
    ) if english_mode else ""

    prompt = QUIZ_PROMPT.format(
        quiz_count=quiz_count,
        history=history_text,
        latest=latest,
        phase=phase,
        english_mode_note=english_mode_note,
    )

    llm_response = await call_llm(
        [{"role": "user", "content": prompt}],
        user=None,
        max_tokens=600,
    )
    log.debug('[%s] quiz: raw LLM response:\n%s', uid, llm_response)

    parsed = _parse_quiz_response(llm_response)
    skill_updates = parsed.get('SKILL_UPDATES', '')
    if skill_updates and skill_updates.lower() != 'none':
        log.info('[%s] quiz: skill updates — %s', uid, skill_updates)
    await _save_skill_updates(user, skill_updates)

    # --- CONCLUDE ---
    if parsed['action'] == 'CONCLUDE':
        cefr = parsed.get('CEFR_LEVEL', 'A2')
        comment = parsed.get('COMMENT', '').strip()

        skill_scores = {}
        if skill_updates and skill_updates.lower() != 'none':
            score_map = {'shaky': 1, 'developing': 2, 'confident': 3, 'mastered': 4}
            for pair in skill_updates.split(','):
                pair = pair.strip()
                if ':' in pair:
                    sid, _, sval = pair.partition(':')
                    score = score_map.get(sval.strip())
                    if score:
                        skill_scores[sid.strip()] = score

        def _skill_label(skill_id):
            parts = skill_id.split('_', 1)
            return parts[1].replace('_', ' ').title() if len(parts) > 1 else skill_id.replace('_', ' ').title()

        strong_items = [f"— {_skill_label(k)}" for k, v in skill_scores.items() if v >= 3]
        weak_items = [f"— {_skill_label(k)}" for k, v in skill_scores.items() if v <= 2]
        focus_skills = [_skill_label(k) for k, v in skill_scores.items() if v <= 2]
        focus = f"We'll start with {', '.join(focus_skills[:2])}." if focus_skills else "We'll build from your current level."

        assessment_parts = [f"📊 **Your Spanish level: {cefr}**\n"]
        if strong_items:
            assessment_parts.append("✅ **Strong:**\n" + "\n".join(strong_items))
        if weak_items:
            assessment_parts.append("⚠️ **Needs work:**\n" + "\n".join(weak_items))
        assessment_parts.append(f"🎯 **First sessions will focus on:** {focus}")
        if comment:
            assessment_parts.append(f"_{comment}_")
        assessment = "\n\n".join(assessment_parts)

        log.info('[%s] quiz: CONCLUDE after %d questions — CEFR=%s skills=%s', uid, quiz_count, cefr, skill_updates)

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
        log.info('[%s] quiz: onboarding complete', uid)

        follow_up = POST_ASSESSMENT_ES if cefr in ('B1', 'B2', 'C1', 'C2') else POST_ASSESSMENT_EN

        return {"text": assessment, "follow_up": follow_up, "audio_url": None, "session_ended": False}

    # --- CONTINUE ---
    next_q_raw = _fix_blanks(parsed.get('NEXT_QUESTION', ''))

    # Silent retry if NEXT_QUESTION missing
    if not next_q_raw:
        log.warning('[%s] quiz: NEXT_QUESTION missing, retrying — raw:\n%s', uid, llm_response)
        llm_response = await call_llm(
            [{"role": "user", "content": prompt}],
            user=None,
            max_tokens=600,
        )
        parsed = _parse_quiz_response(llm_response)
        next_q_raw = _fix_blanks(parsed.get('NEXT_QUESTION', ''))
        if not next_q_raw:
            log.error('[%s] quiz: NEXT_QUESTION missing after retry — raw:\n%s', uid, llm_response)
            return {"text": "Lo siento, algo salió mal. 😅 Can you repeat that?", "audio_url": None, "session_ended": False}

    next_q_display = (GUESSING_REMINDER + next_q_raw) if prepend_reminder else next_q_raw

    log.info('[%s] quiz: Q%d sent — %r', uid, quiz_count + 1, next_q_raw[:80])
    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='quiz',
        dimension='writing',
        content=next_q_raw,
        user_response='',
    )
    return {"text": next_q_display, "audio_url": None, "session_ended": False}
