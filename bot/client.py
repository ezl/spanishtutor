import asyncio
import logging
import traceback
import discord
import django.conf

from engine.dispatch import FALLBACK_ERROR_TEXT, IncomingEvent, handle as dispatch_handle

logger = logging.getLogger('bot')

DISCORD_RETRIES = 1
DISCORD_RETRY_DELAY = 2.0
# Single source of truth lives in engine.dispatch — the transport only needs it
# for failures in its own send path (dispatch already wraps engine errors).
USER_ERROR_MESSAGE = FALLBACK_ERROR_TEXT

_user_locks: dict[str, asyncio.Lock] = {}


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

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f'Luz Angela online as {client.user} (id: {client.user.id})')


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
