import traceback
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
    return user, created


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
    attachments = message.attachments

    if text.lower() == '!reset':
        from learner.models import User
        await sync_to_async(User.objects.filter(discord_id=str(message.author.id)).delete)()
        await sync_to_async(User.objects.create)(discord_id=str(message.author.id), display_name='')
        await message.channel.send(FIRST_MESSAGE)
        return

    if text.lower() == '!retest':
        from learner.models import User, Session
        user_obj = await sync_to_async(User.objects.filter(discord_id=str(message.author.id)).first)()
        if user_obj:
            await sync_to_async(
                lambda: Session.objects.filter(user=user_obj, session_type='onboarding').delete()
            )()
            await sync_to_async(
                User.objects.filter(pk=user_obj.pk).update
            )(onboarding_complete=False, estimated_cefr_level='')
        await message.channel.send(
            "Starting fresh placement quiz! Let's see where you are now.\n\n"
            "Say **listo** when you're ready."
        )
        return

    if text.lower() == '!english':
        from learner.models import User
        await sync_to_async(
            User.objects.filter(discord_id=str(message.author.id)).update
        )(instruction_language='english')
        await message.channel.send("Got it — I'll give all instructions in English from now on.")
        return

    if text.lower() == '!spanish':
        from learner.models import User
        await sync_to_async(
            User.objects.filter(discord_id=str(message.author.id)).update
        )(instruction_language='spanish')
        await message.channel.send("¡Perfecto! De ahora en adelante, todo en español.")
        return

    if text.lower() == '!menu':
        from learner.models import User
        import django.conf
        user_obj = await sync_to_async(User.objects.filter(discord_id=str(message.author.id)).first)()
        base_url = django.conf.settings.BASE_URL
        discord_id = str(message.author.id)
        grid_url = f"{base_url}/progress/{discord_id}/"
        level = f"**{user_obj.estimated_cefr_level}**" if user_obj and user_obj.estimated_cefr_level else "not yet assessed"
        menu = (
            f"**Current level:** {level}\n"
            f"**Skill grid:** {grid_url}\n\n"
            f"**Commands:**\n"
            f"`!retest` — retake the placement quiz\n"
            f"`!english` — force English instructions\n"
            f"`!spanish` — force Spanish instructions\n"
            f"`!reset` — wipe everything and start over\n"
        )
        await message.channel.send(menu)
        return

    try:
        async with message.channel.typing():
            user, is_new = await get_or_create_user(message.author)

            if is_new:
                await message.channel.send(FIRST_MESSAGE)
                return

            from engine.core import handle_message
            result = await handle_message(user, text, list(attachments))

        if result.get('text'):
            response = result['text']
            while response:
                await message.channel.send(response[:1990])
                response = response[1990:]

        if result.get('follow_up'):
            await message.channel.send(result['follow_up'])

        if result.get('dev_log'):
            await message.channel.send(result['dev_log'])

    except Exception as e:
        tb = traceback.format_exc()
        print(f'ERROR handling message from {message.author}: {e}\n{tb}')
        try:
            await message.channel.send(
                f'Lo siento, algo salió mal. 😅 (Error: `{type(e).__name__}: {str(e)[:100]}`)'
            )
        except Exception:
            pass


async def run():
    token = django.conf.settings.DISCORD_BOT_TOKEN
    await client.start(token)
