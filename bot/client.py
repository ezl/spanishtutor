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

# Everyone who has already been pointed at DMs, so the signpost does not shout
# at every message they send. In-memory on purpose: a redeploy costing someone
# a second pointer is cheaper than a table for it.
_guild_redirected: set[str] = set()

# Discord will not let a stranger DM a bot until they share a server, so the
# landing page's Discord CTA is an invite -- it lands people in a room, not in a
# conversation. These two strings are the whole bridge from that room to a DM.
GUILD_REDIRECT_TEXT = (
    "\N{WAVING HAND SIGN} \u00a1Hola! Soy Luz Angela. I teach in a **private chat**, "
    "not here in the server.\n\n"
    "**Click here to message Luz Angela: {dm_url}**\n"
    "Then hit **Message** and say hi \u2014 anything at all.\n\n"
    "(On your phone: tap my picture, then **Message**.)"
)

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
    print(f'Luz Angela online as {client.user} (id: {client.user.id})')


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        await _redirect_to_dm(message)
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


async def _redirect_to_dm(message: discord.Message) -> None:
    """Answer a server message with the way out of the server.

    The engine is never called: a message here is someone looking for the door,
    not a lesson answer, and scoring it as one would be worse than silence.
    """
    uid = str(message.author.id)
    if uid in _guild_redirected:
        return
    _guild_redirected.add(uid)
    try:
        await message.channel.send(GUILD_REDIRECT_TEXT.format(dm_url=_dm_url()))
    except Exception as e:
        _guild_redirected.discard(uid)
        logger.warning('Could not point %s at DMs: %s', message.author, e)


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
        channel = getattr(member.guild, 'system_channel', None)
        if channel is None:
            return
        try:
            await channel.send(CLOSED_DM_TEXT.format(
                mention=member.mention, dm_url=_dm_url()))
        except Exception as e:
            logger.warning('Server fallback failed for %s: %s', member, e)
