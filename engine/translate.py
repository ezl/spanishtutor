from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm

TRANSLATE_TIMEOUT_MINUTES = 10

TRANSLATE_SYSTEM_PROMPT = """You are a translation assistant between English and Colombian Spanish — specifically the casual register spoken by young professionals in Medellín going out, hanging out, making plans, and talking about their lives. Think: rooftop bar, weekend plans, lunch with coworkers, WhatsApp voice notes. Not textbook Spanish. Not formal. Paisa.

Detect the language of the user's input and translate to the other language (English→Spanish or Spanish→English).

Rules:
- Default to casual Medellín register. Use vos or tú as appropriate for this setting (vos is common in Medellín). Use real expressions: parce, bacano, chimba, ¿qué más?, qué pena, listo, dale, etc. where they fit naturally.
- If the input is clearly something to translate: provide translations. Give exactly 1 translation if unambiguous. Give 2-3 if there are meaningful variants (register differences, regional options, or genuinely different meanings). When giving multiple, briefly label each on the same line in parentheses, e.g. "1. rumba (going out, partying)  2. fiesta (more generic)".
- Format: single translation on one line with no preamble. Multiple translations as a numbered list.
- If the input reads like context or clarification rather than something to translate: ask one short clarifying question.
- If ambiguous: make your best inference and translate. Only ask when you genuinely cannot determine intent.
- No preamble. No "Here is the translation:". No praise. Just the translation."""


def _in_translate_mode(user) -> bool:
    """Return True if the user is in an active (non-expired) translate session."""
    if not user.translate_mode_entered_at:
        return False
    elapsed = (timezone.now() - user.translate_mode_entered_at).total_seconds()
    return elapsed < TRANSLATE_TIMEOUT_MINUTES * 60


async def handle_translate(user, text: str) -> dict:
    """Translate user input (EN↔ES) and refresh the sliding timeout."""
    from learner.models import User

    now = timezone.now()
    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(translate_mode_entered_at=now)
    user.translate_mode_entered_at = now

    messages = [{"role": "user", "content": text}]
    response_text = await call_llm(messages, system_override=TRANSLATE_SYSTEM_PROMPT)

    return {"text": response_text, "audio_url": None, "session_ended": False}
