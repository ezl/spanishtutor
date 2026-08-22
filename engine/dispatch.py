"""
Transport-agnostic dispatch layer.

Sits between platform I/O (bot/, messenger/, future transports) and the
engine (engine.core.handle_message). Platform transports normalize their
incoming events into IncomingEvent, hand to dispatch.handle, get back a
list of Reply objects, and send them via their native API.

The engine below dispatch stays untouched.

Text-only by design — voice/attachments live on the web app, not chat
platforms (see 2026-08-17 discussion). IncomingEvent has no attachments
field on purpose; if a future platform needs attachments, this is where
we'd extend.
"""
from dataclasses import dataclass

from asgiref.sync import sync_to_async


@dataclass
class IncomingEvent:
    """A normalized incoming message from any chat platform. Text-only."""
    platform: str        # "discord", "messenger", ...
    external_id: str     # discord user id, messenger PSID, etc.
    display_name: str    # best-effort human name; may be "" for platforms that don't give it
    text: str            # user's message text, stripped


@dataclass
class Reply:
    """A normalized outbound reply. Transport formats + sends via its native API."""
    text: str
    follow_up: str | None = None
    session_ended: bool = False


# ── User resolution ──────────────────────────────────────────────────────────


PLATFORM_ID_FIELD = {
    'discord': 'discord_id',
    'messenger': 'messenger_psid',
}


async def resolve_user(platform: str, external_id: str, display_name: str) -> tuple:
    """Return (User, is_new). Finds or creates a User row by the platform's
    external identity field (User.discord_id or User.messenger_psid).

    Unifies what bot/client.py and messenger/views.py used to each do
    separately — new platforms plug in by adding an entry to
    PLATFORM_ID_FIELD."""
    from learner.models import User

    field = PLATFORM_ID_FIELD.get(platform)
    if field is None:
        raise ValueError(f"Unknown platform: {platform!r}")

    user, created = await sync_to_async(User.objects.get_or_create)(
        **{field: external_id},
        defaults={'display_name': display_name},
    )
    return user, created


# ── Entry point ──────────────────────────────────────────────────────────────


FALLBACK_ERROR_TEXT = "Lo siento, tuve un problema. 😅 Try again in a moment!"

# Chat platforms deliver voice clips, photos and stickers as a message with no
# text. Every transport normalizes that to an empty IncomingEvent.text, and the
# guard in handle() below turns it into this notice — so no transport needs to
# know what an attachment is, and none can leak an empty turn into the engine.
NON_TEXT_NOTICE_TEXT = (
    "¡Ay! I can only read text right now. 📝 Voice and photos are coming to "
    "the web app later — for now, type it out and I'll help!"
)

# Shown when someone with an existing thread re-enters via a platform's start
# affordance (Messenger's Get Started, or an m.me referral from the website
# CTA). Deliberately dumb: it starts no session and calls no model, so the
# engine's own returning-user check-in still fires on their next message and
# stays the only thing that knows what a check-in is.
#
# Spanish first with an English gloss, matching FIRST_MESSAGE — a returning
# student may still be A1, and the one thing they must understand is how to
# begin.
def welcome_back_text(name: str) -> str:
    greeting = f"¡Hola de nuevo, {name}!" if name else "¡Hola de nuevo!"
    return (
        f"👋 {greeting}\n"
        "¿Comenzamos una nueva lección? Di «sí».\n\n"
        "(Ready for a new lesson? Say \"sí\" to start.)"
    )


# ── Commands ─────────────────────────────────────────────────────────────────
# Text-prefix commands the student can type in any chat platform. Handlers
# receive (user, event) and return a list[Reply]. Registered in COMMANDS
# below; dispatch.handle checks incoming text against the table before
# routing to the engine.


async def _cmd_reset(user, event: IncomingEvent) -> list:
    """Wipe the user's learning state and start onboarding over. Keeps the row
    (and its platform identity) — see engine.onboarding.reset_user."""
    from engine.onboarding import FIRST_MESSAGE, reset_user
    await reset_user(user)
    return [Reply(text=FIRST_MESSAGE)]


async def _cmd_retest(user, event: IncomingEvent) -> list:
    """Clear onboarding state so the placement quiz runs again."""
    from learner.models import Session, User
    await sync_to_async(
        lambda: Session.objects.filter(user=user, session_type='onboarding').delete()
    )()
    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(onboarding_complete=False, estimated_cefr_level='')
    return [Reply(text=(
        "Starting fresh placement quiz! Let's see where you are now.\n\n"
        "Say **listo** when you're ready."
    ))]


async def _cmd_english(user, event: IncomingEvent) -> list:
    from learner.models import User
    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(instruction_language='english')
    return [Reply(text="Got it - I'll give all instructions in English from now on.")]


async def _cmd_spanish(user, event: IncomingEvent) -> list:
    from learner.models import User
    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(instruction_language='spanish')
    return [Reply(text="¡Perfecto! De ahora en adelante, todo en español.")]


async def _cmd_menu(user, event: IncomingEvent) -> list:
    """Show current level + skill grid link + command reference."""
    from django.conf import settings
    from learner.auth import make_progress_token
    base_url = settings.BASE_URL
    token = make_progress_token(user.pk)
    grid_url = f"{base_url}/auth/{token}/" if token else None
    level = f"**{user.estimated_cefr_level}**" if user.estimated_cefr_level else "not yet assessed"
    grid_line = f"**Skill grid (valid 1 hr):** {grid_url}\n" if grid_url else ""
    text = (
        f"**Current level:** {level}\n"
        f"{grid_line}\n"
        f"**Commands:**\n"
        f"`!translate` - translate between English and Spanish (times out after 10 min)\n"
        f"`!retest` - retake the placement quiz\n"
        f"`!english` - force English instructions\n"
        f"`!spanish` - force Spanish instructions\n"
        f"`!reset` - wipe everything and start over\n"
    )
    return [Reply(text=text)]


async def _cmd_translate(user, event: IncomingEvent) -> list:
    """Enter translate mode. Closes any active learning session first so we
    don't corrupt state; user's next message will be handled by engine.translate."""
    from django.utils import timezone
    from engine.session import _close_session_record
    from learner.models import Session, User

    active_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .first()
    )()
    if active_session:
        await _close_session_record(active_session, user)

    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(translate_mode_entered_at=timezone.now())

    return [Reply(text=(
        "Translation mode on. Send me anything in English and I'll give you the Spanish, "
        "or Spanish and I'll give you the English. Times out after 10 minutes of inactivity."
    ))]


async def _cmd_learn(user, event: IncomingEvent, arg: str) -> list:
    """`!learn <topic>` — request a specific lesson by name.

    The deterministic counterpart to a natural-language request. Both funnel
    into engine.skill_request so the resolution rules exist in one place.
    """
    from engine.skill_request import handle_skill_request
    from learner.models import Session

    if not arg:
        return [Reply(text=(
            "Tell me what you want to work on: `!learn <topic>` — "
            "for example `!learn subjunctive` or `!learn preterite`. "
            "`!menu` shows what's available."
        ))]

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True)
                               .exclude(session_type='onboarding')
                               .select_related('target_skill').first()
    )()
    result = await handle_skill_request(user, session, arg)
    return [Reply(text=result.get('text', ''),
                  session_ended=result.get('session_ended', False))]


COMMANDS = {
    '!reset': _cmd_reset,
    '!retest': _cmd_retest,
    '!english': _cmd_english,
    '!spanish': _cmd_spanish,
    '!menu': _cmd_menu,
    '!translate': _cmd_translate,
}

# Commands that take an argument. Matched on the FIRST word of the message
# rather than the whole text, so they need their own table.
ARG_COMMANDS = {
    '!learn': _cmd_learn,
}


async def handle_welcome(platform: str, external_id: str, display_name: str = '',
                         ref: str = '') -> list:
    """Entry point for a platform's explicit "start" affordance — Messenger's
    Get Started tap, or an m.me referral from the website CTA — where there is
    no user text to route. Separate from handle() because the trigger carries
    no message: handle() would have to invent one.

    `ref` is the arbitrary payload the platform passes through (m.me's ?ref=),
    logged for attribution only.

    The reply depends on who showed up. One website CTA serves brand-new
    visitors and long-time students alike, so this cannot always answer with
    FIRST_MESSAGE — that opens with "What's your name?", which to an onboarded
    student reads as having lost all their progress.
    """
    import logging
    from engine.onboarding import FIRST_MESSAGE
    from learner.models import Session

    logger = logging.getLogger('engine.dispatch')

    user, created = await resolve_user(platform, external_id, display_name)
    if ref:
        logger.info('welcome: %s/%s via ref=%r', platform, external_id, ref)

    # Onboarding never started. Both halves are load-bearing: Discord seeds
    # display_name from the platform profile at creation, so `created` is the
    # only signal there; Messenger sends no name, so a row can persist with an
    # empty one and still need the name prompt.
    if created or not user.display_name:
        return [Reply(text=FIRST_MESSAGE)]

    # Mid-lesson. Their thread already shows Luz's pending turn — dropping a
    # greeting on top of it is the thing that would actually look broken.
    has_open_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True).exists()
    )()
    if has_open_session:
        return []

    return [Reply(text=welcome_back_text(user.display_name))]


async def handle(event: IncomingEvent) -> list:
    """The single entry point every transport calls with a normalized event.

    Owns:
    - User resolution + creation
    - First-message flow for brand-new users (returns [Reply(FIRST_MESSAGE)])
    - The non-text guard (empty text -> NON_TEXT_NOTICE_TEXT, engine untouched)
    - Command dispatch (the COMMANDS table above — works on every platform)
    - Engine dispatch (routes text to engine.core.handle_message)
    - Error wrapping (any exception returns a friendly fallback Reply)
    - Building Reply list from the engine's response dict

    Returns: list[Reply] the transport should send in order.
    """
    import logging
    from engine.core import handle_message
    from engine.onboarding import FIRST_MESSAGE

    logger = logging.getLogger('engine.dispatch')

    try:
        user, is_new = await resolve_user(
            event.platform, event.external_id, event.display_name,
        )

        if is_new:
            return [Reply(text=FIRST_MESSAGE)]

        # Non-text guard. Sits above command dispatch and the engine so an
        # attachment-only message can never be scored as a (blank) answer.
        # Below the is_new check on purpose: someone whose first ever message
        # is a sticker should be onboarded, not corrected.
        if not event.text.strip():
            return [Reply(text=NON_TEXT_NOTICE_TEXT)]

        # Command dispatch — text-prefix commands short-circuit the engine.
        # Case-insensitive match on the exact stripped text.
        cmd_key = event.text.strip().lower()
        if cmd_key in COMMANDS:
            return await COMMANDS[cmd_key](user, event)

        # Argument-taking commands match on the first word; the rest is the arg.
        head, _, arg = event.text.strip().partition(' ')
        if head.lower() in ARG_COMMANDS:
            return await ARG_COMMANDS[head.lower()](user, event, arg.strip())

        result = await handle_message(user, event.text, [])

        replies = []
        if result.get('text'):
            replies.append(Reply(
                text=result['text'],
                follow_up=result.get('follow_up'),
                session_ended=bool(result.get('session_ended', False)),
            ))
        # dev_log (scoring transcript / debug output at session close) is a
        # separate reply. Not surfaced to end users in prod, but kept intact
        # for developer visibility on Discord.
        if result.get('dev_log'):
            replies.append(Reply(text=result['dev_log']))
        return replies
    except Exception:
        logger.exception('dispatch.handle failed for %s/%s',
                         event.platform, event.external_id)
        return [Reply(text=FALLBACK_ERROR_TEXT)]
