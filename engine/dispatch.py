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
from dataclasses import dataclass, field


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
