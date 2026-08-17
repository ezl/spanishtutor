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
