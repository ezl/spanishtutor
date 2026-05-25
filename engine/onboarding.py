"""
Onboarding: new user → adaptive placement quiz → skill grid + CEFR estimate.

Quiz uses a three-pass probe algorithm (engine/quiz_flow.py) with pre-built
questions from the QuizQuestion bank. LLM is used only for scoring answers
(engine/quiz_evaluator.py) — not question generation or flow control.
"""
import logging
import re
from asgiref.sync import sync_to_async
from django.conf import settings
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
    "I'm just here to assess your Spanish right now. "
    "I'll be a better conversation partner once we're done! "
    "Let's continue:\n\n{question}"
)

GUESSING_REMINDER = (
    "Quick reminder: if you're not sure, just say *I don't know* or *no sé*. "
    "No penalty, it actually helps me place you better.\n\n"
)

MENU_MESSAGE = """No worries! Here are your options:

**1.** Start over - reset the quiz from the beginning
**2.** English mode - I'll give instructions in English from here
**3.** How does this work? - explain what I'm doing and why

Reply with 1, 2, or 3."""

HOW_IT_WORKS = (
    "I'm giving you a short placement quiz to figure out your Spanish level, "
    "somewhere between A1 (beginner) and C2 (fluent). I'll ask a few questions, "
    "starting easy and adjusting based on your answers. Should take about 2-3 minutes. "
    "Once I have a clear picture, we'll start actual lessons tailored to exactly where you are. "
    "Just answer as best you can. If you don't know, say *no sé*."
)

POST_ASSESSMENT_EN = (
    'Listo! Now the real fun starts. Any time you want to practice, just say something like '
    '*"let\'s do a lesson"* or *"teach me something"* and I\'ll take it from there.\n\n'
    "If you're ready for a lesson, try it now by asking me to start a lesson!"
)

POST_ASSESSMENT_ES = (
    '¡Listo! Ahora empieza la diversión. Cuando quieras practicar, solo dime algo como '
    '*"hagamos una lección"* o *"enséñame algo"* y yo me encargo.\n\n'
    '¡Si estás listo para una lección, pruébalo ahora y pídeme que empecemos!'
)

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
        return {"text": f"Got it - I'll give instructions in English from here.{reask}", "audio_url": None, "session_ended": False}

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
        f"Hola, **{name}**!\n\n"
        f"I'll ask you a few questions to get a sense of your Spanish level. It should take 3–5 minutes.\n\n"
        f"When you're ready, say **\"Listo\"** (\"Ready\" in Spanish 😊)"
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

def _format_question(question, cefr_level: str = 'A1') -> str:
    """Format a QuizQuestion for display to the user."""
    # Strip any options or 'don't know' footer the LLM may have embedded in question_text
    raw_lines = question.question_text.splitlines()
    stem_lines = []
    for line in raw_lines:
        stripped = line.strip()
        if re.match(r'^\*?\*?[a-dA-D][.)]\*?\*?\s', stripped):
            break
        if "don't know" in stripped.lower() or "i don't know" in stripped.lower():
            break
        stem_lines.append(line)
    text = '\n'.join(stem_lines).rstrip()

    if question.format == 'multiple_choice' and question.options:
        lines = [text]
        for key in ['a', 'b', 'c', 'd']:
            if key in question.options:
                lines.append(f"  **{key})** {question.options[key]}")
        if cefr_level in ('B1', 'B2', 'C1', 'C2'):
            lines.append('\n*(¿No estás seguro? Di "no sé" — es más útil que adivinar)*')
        else:
            lines.append('\n*(Not sure? Say "I don\'t know" — it\'s more useful than guessing)*')
        return '\n'.join(lines)
    return text


async def _draw_question(skills: list, skill_idx: int, quiz_state: dict):
    """Draw a question from skills[skill_idx], falling back to adjacent skills."""
    asked = quiz_state.get('asked_question_ids', [])
    for offset in [0, -1, 1, -2, 2]:
        idx = skill_idx + offset
        if idx < 0 or idx >= len(skills):
            continue
        skill = skills[idx]
        question = await sync_to_async(
            lambda s=skill: s.quiz_questions.filter(active=True).exclude(pk__in=asked).order_by('?').first()
        )()
        if question:
            return question, idx
    return None, skill_idx


async def _conclude_quiz(user, session, quiz_state: dict, skills: list, uid: str) -> dict:
    from learner.models import EvaluationProgress, Session, SessionEvent, QuizQuestion
    from .quiz_flow import quiz_derive_results
    from .interests import seed_interests

    results = quiz_derive_results(quiz_state, skills)
    cefr = results['cefr_level']
    skill_scores = results['skill_scores']

    def _skill_label(skill_id):
        parts = skill_id.split('_', 1)
        return parts[1].replace('_', ' ').title() if len(parts) > 1 else skill_id.replace('_', ' ').title()

    strong_items = [f"- {_skill_label(k)}" for k, v in skill_scores.items() if v >= 3]
    weak_items   = [f"- {_skill_label(k)}" for k, v in skill_scores.items() if v <= 2]
    focus_skills = [_skill_label(k) for k, v in skill_scores.items() if v <= 2]
    focus = f"We'll start with {', '.join(focus_skills[:2])}." if focus_skills else "We'll build from your current level."

    assessment_parts = [f"📊 **Your Spanish level: {cefr}**\n"]
    if strong_items:
        assessment_parts.append("✅ **Strong:**\n" + "\n".join(strong_items))
    if weak_items:
        assessment_parts.append("⚠️ **Needs work:**\n" + "\n".join(weak_items))
    assessment_parts.append(f"🎯 **First sessions will focus on:** {focus}")
    assessment = "\n\n".join(assessment_parts)

    # Build per-question review summary
    quiz_events = await sync_to_async(
        lambda: list(SessionEvent.objects.filter(session=session, event_type='quiz').order_by('timestamp'))
    )()
    question_pks = [int(e.skill_id) for e in quiz_events if e.skill_id and e.skill_id.isdigit()]
    questions_by_pk = {}
    if question_pks:
        qs = await sync_to_async(
            lambda: {q.pk: q for q in QuizQuestion.objects.filter(pk__in=question_pks).select_related('skill')}
        )()
        questions_by_pk = qs

    score_labels = {1: '🟥 miss', 2: '🟨 partial', 3: '🟦 good', 4: '🟩 correct'}
    review_lines = ['---', '**Quiz review:**', '']
    for i, event in enumerate(quiz_events, 1):
        q = questions_by_pk.get(int(event.skill_id)) if event.skill_id and event.skill_id.isdigit() else None
        score = event.score_delta
        score_str = score_labels.get(score, '—') if score is not None else '—'
        skill_name = q.skill.name if q else '—'

        review_lines.append(f"**Q{i}** — {skill_name} ({score_str})")
        review_lines.append(f"  You said: *{event.user_response or '(no answer)'}*")
        if q:
            if q.format == 'multiple_choice' and q.options:
                correct_text = q.options.get(q.correct_answer, q.correct_answer)
                review_lines.append(f"  Expected: **{q.correct_answer})** {correct_text}")
            else:
                review_lines.append(f"  Expected: **{q.correct_answer}**")
                if q.rubric:
                    review_lines.append(f"  Acceptable: {q.rubric}")
        review_lines.append('')

    grid_url = f"{settings.BASE_URL}/progress/"
    review_lines.append(f"[View your full skill grid]({grid_url})")
    review_summary = '\n'.join(review_lines)

    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(
        estimated_cefr_level=cefr,
        onboarding_complete=True,
    )
    await seed_interests(user)
    await sync_to_async(EvaluationProgress.objects.get_or_create)(user=user, phase='session1')
    await sync_to_async(
        lambda: Session.objects.filter(pk=session.pk).update(ended_at=timezone.now())
    )()
    log.info('[%s] quiz: complete — CEFR=%s questions=%d', uid, cefr, quiz_state['question_count'])

    full_text = assessment + '\n\n' + review_summary
    follow_up = POST_ASSESSMENT_ES if cefr in ('B1', 'B2', 'C1', 'C2') else POST_ASSESSMENT_EN
    return {"text": full_text, "follow_up": follow_up, "audio_url": None, "session_ended": False}


async def _step_adaptive_quiz(user, text: str) -> dict:
    from learner.models import Session, SessionEvent, Skill, SkillScore, QuizQuestion
    from .quiz_flow import quiz_initial_state, quiz_select_skill_idx, quiz_update_state, quiz_is_done
    from .quiz_evaluator import evaluate_answer

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

    # Load all events for state inspection
    all_events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp'))
    )()
    quiz_events = [e for e in all_events if e.event_type == 'quiz']

    # --- Menu response state ---
    last_event = all_events[-1] if all_events else None
    if last_event and last_event.event_type == 'menu' and not last_event.user_response:
        return await _handle_menu_response(user, session, text, last_event, quiz_events)

    # Load ordered skill ladder
    skills = await sync_to_async(
        lambda: list(Skill.objects.filter(active=True).order_by('order'))
    )()

    # Load or initialize quiz_state
    quiz_state = session.quiz_state
    if not quiz_state:
        quiz_state = quiz_initial_state(len(skills))
        log.info('[%s] quiz: initialized — step=%d skills=%d', uid, quiz_state['step_size'], len(skills))

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
            log.info('[%s] quiz: Q%d answer — %r', uid, len(quiz_events), text[:60])
            await sync_to_async(
                lambda: SessionEvent.objects.filter(pk=quiz_events[-1].pk).update(user_response=text)
            )()
            quiz_events[-1].user_response = text

        # Evaluate and score the answer
        current_question_id = quiz_state.get('current_question_id')
        current_skill_idx = quiz_state.get('current_skill_idx')
        if current_question_id is not None and current_skill_idx is not None:
            try:
                question = await sync_to_async(QuizQuestion.objects.get)(pk=current_question_id)
                score = 1 if input_class == 'dont_know' else await evaluate_answer(question, text)
                log.info('[%s] quiz: skill_idx=%d score=%d', uid, current_skill_idx, score)
                quiz_state = quiz_update_state(quiz_state, current_skill_idx, score)
                # Store score on the event so the completion summary can show it
                if quiz_events:
                    await sync_to_async(
                        lambda s=score: SessionEvent.objects.filter(pk=quiz_events[-1].pk).update(score_delta=s)
                    )()
                skill = skills[current_skill_idx]
                await sync_to_async(SkillScore.objects.update_or_create)(
                    user=user, skill=skill, mode='writing',
                    defaults={'score': score, 'last_tested_at': timezone.now()},
                )
            except (QuizQuestion.DoesNotExist, IndexError):
                log.warning('[%s] quiz: could not score — question_id=%s', uid, current_question_id)

    # Check if quiz is complete
    if quiz_is_done(quiz_state):
        return await _conclude_quiz(user, session, quiz_state, skills, uid)

    # Select next skill index and draw question
    skill_idx = quiz_select_skill_idx(quiz_state)
    question, chosen_idx = await _draw_question(skills, skill_idx, quiz_state)

    if question is None:
        if quiz_state['question_count'] == 0:
            log.error('[%s] quiz: no active QuizQuestion rows found — question bank is empty', uid)
            return {
                "text": (
                    "Uh oh — I don't have any assessment questions loaded. "
                    "This isn't your fault, it's a setup problem on our end. "
                    "Please contact support and let them know so it can be fixed."
                ),
                "audio_url": None,
                "session_ended": False,
            }
        log.warning('[%s] quiz: no question available near idx=%d — concluding early', uid, skill_idx)
        quiz_state['pass'] = 'done'
        return await _conclude_quiz(user, session, quiz_state, skills, uid)

    # Update state
    quiz_state['current_question_id'] = question.pk
    quiz_state['current_skill_idx'] = chosen_idx
    quiz_state['asked_question_ids'] = list(quiz_state['asked_question_ids']) + [question.pk]

    # Guessing reminder: 3+ scores of 1 in state, not yet shown this quiz
    prepend_reminder = False
    reminder_shown = any(e.event_type == 'meta' and e.content == 'guessing_reminder_shown' for e in all_events)
    if not reminder_shown and not is_first:
        shaky_count = sum(1 for v in quiz_state['scores'].values() if v <= 1)
        if shaky_count >= 3:
            log.info('[%s] quiz: guessing reminder triggered (%d shaky scores)', uid, shaky_count)
            await sync_to_async(SessionEvent.objects.create)(
                session=session, event_type='meta',
                content='guessing_reminder_shown', user_response='set',
            )
            prepend_reminder = True

    # Save state and record quiz event
    await sync_to_async(Session.objects.filter(pk=session.pk).update)(quiz_state=quiz_state)
    question_text = _format_question(question, cefr_level=skills[chosen_idx].cefr_level)
    question_display = (GUESSING_REMINDER + question_text) if prepend_reminder else question_text

    log.info('[%s] quiz: Q%d sent — skill_idx=%d %r', uid, len(quiz_events) + 1, chosen_idx, question_text[:80])
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='quiz', dimension='writing',
        skill_id=str(question.pk), content=question_text, user_response='',
    )
    return {"text": question_display, "audio_url": None, "session_ended": False}
