"""
Onboarding flow: new user → 3 questions → adaptive quiz → freeform response → CEFR estimate.
State is tracked via user fields (native_language set = Q1 done, etc.)
"""
from asgiref.sync import sync_to_async
from .core import call_llm

FIRST_MESSAGE = """Hey! I'm Luz Angela, your Spanish tutor.

Quick question to get started — why are you learning Spanish?

*(Tip: send `!reset` anytime to start over.)*"""


async def handle_onboarding(user, text: str, attachments: list = None) -> dict:
    # Determine which onboarding step we're on based on what's already saved
    if not user.native_language:
        return await _step_native_language(user, text)
    elif not user.interests:
        return await _step_interests(user, text)
    elif not user.target_use:
        return await _step_target_use(user, text)
    elif not user.estimated_cefr_level:
        return await _step_adaptive_quiz(user, text)
    else:
        return await _step_freeform(user, text)


async def _step_native_language(user, text: str) -> dict:
    # First real response after FIRST_MESSAGE — extract native language and why learning
    messages = [
        {"role": "user", "content": text},
        {"role": "assistant", "content": ""},
    ]
    # Parse their answer to extract native language
    parse_prompt = f"""The student responded to "what is your native language and why are you learning Spanish?" with:
"{text}"

Extract:
1. native_language: their native language (just the language name, e.g. "English")
2. why_learning: their reason for learning Spanish (brief, their words)

Respond in this exact format:
NATIVE_LANGUAGE: <language>
WHY_LEARNING: <reason>"""

    client_response = await call_llm(
        [{"role": "user", "content": parse_prompt}],
        user=None,
    )

    native_language = "English"
    why_learning = text
    for line in client_response.split('\n'):
        if line.startswith('NATIVE_LANGUAGE:'):
            native_language = line.split(':', 1)[1].strip()
        elif line.startswith('WHY_LEARNING:'):
            why_learning = line.split(':', 1)[1].strip()

    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(
        native_language=native_language,
        why_learning=why_learning,
    )
    await sync_to_async(user.refresh_from_db)()

    prompt = f'The student said: "{text}". Respond in English. Briefly acknowledge (1 sentence), then ask ONE question about their hobbies or interests.'
    response = await call_llm([{"role": "user", "content": prompt}], user=user)

    return {"text": response, "audio_url": None, "session_ended": False}


async def _step_interests(user, text: str) -> dict:
    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(interests=text)
    await sync_to_async(user.refresh_from_db)()

    prompt = f'The student said their interests are: "{text}". Respond in English. React briefly (1 sentence), then ask ONE question about where or how they want to use their Spanish (travel, work, family, etc).'
    response = await call_llm([{"role": "user", "content": prompt}], user=user)
    return {"text": response, "audio_url": None, "session_ended": False}


async def _step_target_use(user, text: str) -> dict:
    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(target_use=text)
    await sync_to_async(user.refresh_from_db)()

    prompt = f'The student said they want to use Spanish for: "{text}". Respond in English. Acknowledge in 1 sentence, then tell them you\'ll ask a couple quick questions to see where they\'re starting from, and ask the first one: what does "hola" mean? Give 4 options. Keep it casual.'
    response = await call_llm([{"role": "user", "content": prompt}], user=user)
    return {"text": response, "audio_url": None, "session_ended": False}


async def _step_adaptive_quiz(user, text: str) -> dict:
    """
    Adaptive quiz. Uses LLM to evaluate answer, determine next question difficulty,
    and eventually estimate CEFR level.
    """
    from learner.models import Session, SessionEvent

    # Get current session for this quiz
    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, session_type='onboarding', ended_at__isnull=True).first()
    )()
    if not session:
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding'
        )

    # Count quiz events so far
    quiz_count = await sync_to_async(
        lambda: session.events.filter(event_type='quiz').count()
    )()

    # Get conversation history
    events = await sync_to_async(
        lambda: list(session.events.filter(event_type='quiz').order_by('timestamp'))
    )()

    history = []
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})

    # Ask LLM to evaluate and continue or conclude
    eval_prompt = f"""You are running an adaptive Spanish placement quiz. Assume the student is a beginner (A1) unless their answers show otherwise. Start easy and only go harder if they're clearly correct. The quiz runs in English. Max 5 questions.

Previous Q&A:
{chr(10).join(f"Q: {e.content} | A: {e.user_response}" for e in events)}

Latest answer: "{text}"

Based on all answers so far:
1. Evaluate correctness
2. Decide: continue (if fewer than 5 questions and level not clear) OR conclude

If continuing: next question should be slightly harder if correct, easier if wrong. Questions in English with Spanish words to translate/identify. Format:
CONTINUE
FEEDBACK: <1 sentence in English>
NEXT_QUESTION: <question in English>

If concluding (at least 3 questions answered):
CONCLUDE
CEFR_LEVEL: <A1|A2|B1|B2|C1|C2>
FEEDBACK: <1 encouraging sentence in English telling them their level>
TRANSITION: <1 sentence in English transitioning to a short freeform exercise>"""

    eval_response = await call_llm(
        [{"role": "user", "content": eval_prompt}],
        user=None,
        max_tokens=512,
    )

    # Log the user's answer to the last quiz event
    if events:
        last_event = events[-1]
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=last_event.pk).update(user_response=text)
        )()

    if eval_response.startswith('CONCLUDE'):
        lines = {l.split(':', 1)[0].strip(): l.split(':', 1)[1].strip()
                 for l in eval_response.split('\n') if ':' in l}
        cefr = lines.get('CEFR_LEVEL', 'A2')
        feedback = lines.get('FEEDBACK', '')
        transition = lines.get('TRANSITION', '')

        await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(
            estimated_cefr_level=cefr
        )
        await sync_to_async(user.refresh_from_db)()

        freeform_prompt = f"\n\n{transition}\n\nTry writing 1-2 sentences about yourself in Spanish — anything at all. Don't worry about mistakes."
        return {"text": f"{feedback}{freeform_prompt}", "audio_url": None, "session_ended": False}

    else:
        lines = {l.split(':', 1)[0].strip(): l.split(':', 1)[1].strip()
                 for l in eval_response.split('\n') if ':' in l}
        feedback = lines.get('FEEDBACK', '')
        next_q = lines.get('NEXT_QUESTION', '')

        await sync_to_async(SessionEvent.objects.create)(
            session=session,
            event_type='quiz',
            dimension='writing',
            content=next_q,
            user_response='',
        )

        return {"text": f"{feedback}\n\n{next_q}", "audio_url": None, "session_ended": False}


async def _step_freeform(user, text: str) -> dict:
    """Evaluate freeform Spanish, finalize onboarding."""
    from learner.models import EvaluationProgress

    eval_prompt = f"""The student (estimated level: {user.estimated_cefr_level}) wrote this freeform Spanish:
"{text}"

Evaluate their actual production ability. Does the CEFR estimate seem right, too high, or too low?
Respond with:
ADJUSTED_LEVEL: <A1|A2|B1|B2|C1|C2>
NOTES: <brief internal notes on what you observed>"""

    eval_response = await call_llm(
        [{"role": "user", "content": eval_prompt}],
        user=None,
        max_tokens=256,
    )

    adjusted_level = user.estimated_cefr_level
    for line in eval_response.split('\n'):
        if line.startswith('ADJUSTED_LEVEL:'):
            adjusted_level = line.split(':', 1)[1].strip()

    await sync_to_async(user.__class__.objects.filter(pk=user.pk).update)(
        estimated_cefr_level=adjusted_level,
        onboarding_complete=True,
    )
    await sync_to_async(EvaluationProgress.objects.get_or_create)(
        user=user, phase='session1'
    )
    await sync_to_async(user.refresh_from_db)()

    closing_prompt = f'The student (level: {adjusted_level}) just finished their placement. In English, give a warm 2-sentence closing: tell them their level and that you\'re ready to start teaching. Keep it encouraging and casual.'
    closing = await call_llm([{"role": "user", "content": closing_prompt}], user=user)

    return {"text": closing, "audio_url": None, "session_ended": True}
