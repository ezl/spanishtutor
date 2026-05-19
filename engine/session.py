"""
Post-onboarding session handling: open, content loop, close.
"""
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm

GOODBYE_WORDS = {'bye', 'adiós', 'adios', 'chao', 'hasta luego', 'goodbye', 'ciao', 'nos vemos'}


def _is_goodbye(text: str) -> bool:
    return text.strip().lower() in GOODBYE_WORDS


async def handle_session(user, text: str, attachments: list = None) -> dict:
    from learner.models import Session

    if _is_goodbye(text):
        return await _close_session(user, explicit=True)

    # Get or create active session
    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .first()
    )()

    if not session:
        return await _open_session(user, text)

    return await _continue_session(user, session, text, attachments)


async def _open_session(user, text: str) -> dict:
    from learner.models import Session, SkillScore

    # Find most recent completed session for recap
    last_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=False)
                               .exclude(session_type='onboarding')
                               .order_by('-ended_at').first()
    )()

    # Find weakest / most overdue skill for recommendation
    overdue = await sync_to_async(
        lambda: SkillScore.objects.filter(user=user)
                                  .order_by('next_review_at')
                                  .first()
    )()

    recap = f"Última vez trabajamos en {last_session.summary[:80]}..." if last_session and last_session.summary else ""
    recommendation = f"tu skill más urgente es **{overdue.skill_id}**" if overdue else "empezar con vocabulario básico"

    prompt = f"""The student just started a new session. {recap}
Based on their profile (level: {user.estimated_cefr_level}), the most overdue skill is: {recommendation}.

Generate the session opening message from Luz Angela:
- Greet them warmly (use their name if known, otherwise just hola)
- Reference what they worked on last time (if any)
- State your recommendation: review or push forward, and why (decay/edge of ability)
- Ask: "¿Revisamos o seguimos con algo nuevo?"
Keep it natural and brief — 3-4 sentences max."""

    opening = await call_llm([{"role": "user", "content": prompt}], user=user)

    return {"text": opening, "audio_url": None, "session_ended": False}


async def _continue_session(user, session, text: str, attachments: list = None) -> dict:
    from learner.models import SessionEvent

    # Log user response
    last_event = await sync_to_async(
        lambda: session.events.order_by('-timestamp').first()
    )()
    if last_event and not last_event.user_response:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=last_event.pk).update(user_response=text)
        )()

    # Build conversation history
    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:20])
    )()
    history = []
    for e in events:
        history.append({"role": "assistant", "content": e.content})
        if e.user_response:
            history.append({"role": "user", "content": e.user_response})
    history.append({"role": "user", "content": text})

    response_text = await call_llm(history, user=user)

    await sync_to_async(SessionEvent.objects.create)(
        session=session,
        event_type='conversation',
        content=response_text,
        user_response='',
    )

    return {"text": response_text, "audio_url": None, "session_ended": False}


async def _close_session(user, explicit: bool = True) -> dict:
    from learner.models import Session

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .first()
    )()

    summary_prompt = """The student is ending the session. Generate a warm closing message from Luz Angela in Spanish (calibrated to their level):
- Brief summary of what was covered today
- One specific thing they did well
- What you recommend for next session
- Warm goodbye

Format: "¡Buena sesión, [name]! Hoy trabajamos en ____..." Keep it to 4-5 sentences."""

    summary = await call_llm([{"role": "user", "content": summary_prompt}], user=user)

    if session:
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk).update(
                ended_at=timezone.now(),
                summary=summary[:500],
            )
        )()

    return {"text": summary, "audio_url": None, "session_ended": True}
