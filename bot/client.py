import asyncio
import logging
import traceback
import discord
import django.conf

from engine.dispatch import (
    FALLBACK_ERROR_TEXT,
    IncomingEvent,
    handle as dispatch_handle,
    handle_welcome as dispatch_welcome,
)

logger = logging.getLogger('bot')

DISCORD_RETRIES = 1
DISCORD_RETRY_DELAY = 2.0
# Single source of truth lives in engine.dispatch — the transport only needs it
# for failures in its own send path (dispatch already wraps engine errors).
USER_ERROR_MESSAGE = FALLBACK_ERROR_TEXT

_user_locks: dict[str, asyncio.Lock] = {}

# Shown in the server when a new member's DMs are closed to us. Same
# instruction, minus the part we cannot do for them.
CLOSED_DM_TEXT = (
    "{mention} \u00a1Bienvenido! I tried to send you a message and your DMs are "
    "closed, so you'll have to open the door: **{dm_url}** \u2192 **Message**.\n\n"
    "If that doesn't work, turn on *Allow direct messages from server members* in "
    "this server's privacy settings, then say hi."
)


def _dm_url() -> str:
    """Deep link to the bot's own profile -- the shortest route to a DM box."""
    return f"https://discord.com/users/{client.user.id}"


_DISCORD_MAX = 1990


def _chunk(text: str) -> list[str]:
    """Split text into Discord-safe chunks, breaking on newlines where possible."""
    chunks = []
    while len(text) > _DISCORD_MAX:
        split = text.rfind('\n', 0, _DISCORD_MAX)
        if split == -1:
            split = _DISCORD_MAX
        chunks.append(text[:split])
        text = text[split:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks


def _discord_safe(text: str) -> str:
    """Replace fill-in-blank underscores with a Discord-safe equivalent.
    Eight underscores in a row are eaten by Discord's markdown parser (rendered as invisible underline).
    Using a visible dash sequence avoids this without changing the visual meaning."""
    import re
    return re.sub(r'_{4,}', r'\_\_\_\_\_\_\_\_', text)


async def _send(channel, text: str) -> None:
    """Send a message (chunked if needed) with one retry on transient Discord errors."""
    text = _discord_safe(text)
    for chunk in _chunk(text):
        for attempt in range(DISCORD_RETRIES + 1):
            try:
                await channel.send(chunk)
                break
            except discord.DiscordServerError as exc:
                if attempt < DISCORD_RETRIES:
                    logger.warning('Discord send failed (attempt %d/%d), retrying in %.0fs: %s',
                                   attempt + 1, DISCORD_RETRIES + 1, DISCORD_RETRY_DELAY, exc)
                    await asyncio.sleep(DISCORD_RETRY_DELAY)
                else:
                    raise


intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
# Privileged. on_member_join never fires without it, and it must ALSO be
# switched on under Privileged Gateway Intents in the Discord developer
# portal -- the library cannot ask for what the application does not grant,
# and the failure is silent: joins simply never arrive.
intents.members = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f'Luz Ángela online as {client.user} (id: {client.user.id})')


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return

    text = message.content.strip()

    uid = str(message.author.id)
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()

    try:
        async with _user_locks[uid]:
            async with message.channel.typing():
                event = IncomingEvent(
                    platform='discord',
                    external_id=uid,
                    display_name=message.author.display_name,
                    text=text,
                )
                replies = await dispatch_handle(event)

        for reply in replies:
            if reply.text:
                await _send(message.channel, reply.text)
            if reply.follow_up:
                await _send(message.channel, reply.follow_up)

    except Exception as e:
        logger.error('Error handling message from %s: %s\n%s',
                     message.author, e, traceback.format_exc())
        try:
            await message.channel.send(USER_ERROR_MESSAGE)
        except Exception:
            pass


async def run():
    token = django.conf.settings.DISCORD_BOT_TOKEN
    await client.start(token)


def _fallback_channel(guild):
    """Somewhere the bot can speak when a member's DMs are shut.

    The system channel is the right answer when one is set, but a guild need not
    have one -- this one did not, and the old code returned silently in exactly
    the case the fallback existed for. Any channel the bot can post in beats
    saying nothing.
    """
    channel = getattr(guild, 'system_channel', None)
    if channel is not None:
        return channel
    me = getattr(guild, 'me', None)
    for candidate in getattr(guild, 'text_channels', None) or []:
        try:
            if candidate.permissions_for(me).send_messages:
                return candidate
        except Exception:
            continue
    return None


@client.event
async def on_member_join(member: discord.Member) -> None:
    """Accepting the invite IS the start affordance, so the conversation opens
    itself rather than waiting for someone to discover DMs on their own.

    dispatch.handle_welcome is the same entry point Messenger's Get Started
    button uses; it answers correctly for a returning student too, who would
    read FIRST_MESSAGE as having lost all their progress.
    """
    if getattr(member, 'bot', False):
        return

    try:
        replies = await dispatch_welcome('discord', str(member.id), member.display_name)
    except Exception as e:
        logger.error('welcome failed for %s: %s\n%s', member, e, traceback.format_exc())
        return

    try:
        for reply in replies:
            if reply.text:
                await _send(member, reply.text)
            if reply.follow_up:
                await _send(member, reply.follow_up)
    except discord.Forbidden:
        # Closed DMs. Say it in the one place they can still hear us.
        logger.info('DMs closed for %s; falling back to the server', member)
        channel = _fallback_channel(member.guild)
        if channel is None:
            # Nothing to be done, but not nothing to say: a member is now sitting
            # in the server with no greeting and no way to know why.
            logger.warning(
                'No channel to post the closed-DM notice for %s: nowhere the bot '
                'can speak in %s', member, member.guild)
            return
        try:
            await channel.send(CLOSED_DM_TEXT.format(
                mention=member.mention, dm_url=_dm_url()))
        except Exception as e:
            logger.warning('Server fallback failed for %s: %s', member, e)
