import discord
import django.conf
from asgiref.sync import sync_to_async
from engine.onboarding import FIRST_MESSAGE


intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

client = discord.Client(intents=intents)


async def get_or_create_user(discord_user: discord.User):
    from learner.models import User
    user, created = await sync_to_async(User.objects.get_or_create)(
        discord_id=str(discord_user.id),
        defaults={'display_name': discord_user.display_name},
    )
    if created:
        # Update display name in case it changed
        pass
    return user, created


@client.event
async def on_ready():
    print(f'Luz Angela online as {client.user} (id: {client.user.id})')


@client.event
async def on_message(message: discord.Message):
    # Only respond to DMs, never to ourselves
    if message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return

    text = message.content.strip()
    attachments = message.attachments

    async with message.channel.typing():
        user, is_new = await get_or_create_user(message.author)

        if is_new:
            await message.channel.send(FIRST_MESSAGE)
            return

        from engine.core import handle_message
        result = await handle_message(user, text, list(attachments))

    if result.get('text'):
        # Split long responses to stay under Discord's 2000 char limit
        response = result['text']
        while response:
            await message.channel.send(response[:1990])
            response = response[1990:]


async def run():
    token = django.conf.settings.DISCORD_BOT_TOKEN
    await client.start(token)
