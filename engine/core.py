import asyncio
import logging
import anthropic
import django.conf
from asgiref.sync import sync_to_async
from .persona import get_system_prompt

logger = logging.getLogger('engine')

LLM_RETRIES = 1
LLM_RETRY_DELAY = 2.0


def _is_retryable_llm_error(exc) -> bool:
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return True
    return False


def get_anthropic_client():
    return anthropic.Anthropic(api_key=django.conf.settings.ANTHROPIC_API_KEY)


async def call_llm(messages: list, user=None, max_tokens: int = 1024, system_suffix: str = None) -> str:
    client = get_anthropic_client()
    system = get_system_prompt(user)
    if system_suffix:
        system = system + "\n\n" + system_suffix

    last_exc = None
    for attempt in range(LLM_RETRIES + 1):
        try:
            response = await sync_to_async(client.messages.create)(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except Exception as exc:
            last_exc = exc
            if attempt < LLM_RETRIES and _is_retryable_llm_error(exc):
                logger.warning('LLM call failed (attempt %d/%d), retrying in %.0fs: %s',
                               attempt + 1, LLM_RETRIES + 1, LLM_RETRY_DELAY, exc)
                await asyncio.sleep(LLM_RETRY_DELAY)
            else:
                raise


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
