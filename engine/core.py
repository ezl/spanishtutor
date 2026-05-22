import anthropic
import django.conf
from asgiref.sync import sync_to_async
from .persona import get_system_prompt


def get_anthropic_client():
    return anthropic.Anthropic(api_key=django.conf.settings.ANTHROPIC_API_KEY)


async def call_llm(messages: list, user=None, max_tokens: int = 1024, system_suffix: str = None) -> str:
    client = get_anthropic_client()
    system = get_system_prompt(user)
    if system_suffix:
        system = system + "\n\n" + system_suffix
    response = await sync_to_async(client.messages.create)(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return response.content[0].text


async def handle_message(user, text: str, attachments: list = None) -> dict:
    """
    Interface-agnostic message handler. Called by Discord bot and future web API.

    Returns: {
        "text": str,           # Luz Angela's response text
        "audio_url": str|None, # TTS audio URL if applicable
        "session_ended": bool,
    }
    """
    from learner.models import Session, SessionEvent
    from .onboarding import handle_onboarding
    from .session import handle_session

    if not user.onboarding_complete:
        return await handle_onboarding(user, text, attachments)

    return await handle_session(user, text, attachments)
