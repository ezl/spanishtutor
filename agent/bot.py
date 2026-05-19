import discord
import asyncio
import subprocess
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

TOKEN = os.getenv("DISCORD_DEV_BOT_TOKEN")
REPO_PATH = os.getenv("REPO_PATH", "/root/spanishtutor")

ACTION_CHANNELS = {
    int(os.getenv("DISCORD_DEV_CHANNEL_ID")): "dev",
    int(os.getenv("DISCORD_DEV_DEPLOY_CHANNEL_ID")): "deploy",
    int(os.getenv("DISCORD_DEV_BUGS_CHANNEL_ID")): "bugs",
    int(os.getenv("DISCORD_DEV_FEATURES_CHANNEL_ID")): "features",
}
LOGS_CHANNEL_ID = int(os.getenv("DISCORD_DEV_LOGS_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def run_claude(instruction: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "claude", "--print", instruction,
        cwd=REPO_PATH,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode().strip()
    if not output and stderr:
        output = stderr.decode().strip()
    return output or "No output."


def chunk(text: str, size: int = 1900):
    """Split text into Discord-safe chunks."""
    return [text[i:i+size] for i in range(0, len(text), size)]


@client.event
async def on_ready():
    logs = client.get_channel(LOGS_CHANNEL_ID)
    print(f"Agent online as {client.user}")
    if logs:
        await logs.send(f"Agent online.")


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.channel.id not in ACTION_CHANNELS:
        return

    channel_type = ACTION_CHANNELS[message.channel.id]
    instruction = message.content.strip()
    if not instruction:
        return

    await message.add_reaction("⏳")

    logs = client.get_channel(LOGS_CHANNEL_ID)

    try:
        result = await asyncio.wait_for(run_claude(instruction), timeout=300)
        await message.remove_reaction("⏳", client.user)
        await message.add_reaction("✅")

        for part in chunk(result):
            await message.reply(f"```\n{part}\n```")

        if logs and message.channel.id != LOGS_CHANNEL_ID:
            await logs.send(f"[#{channel_type}] `{instruction[:100]}` → done")

    except asyncio.TimeoutError:
        await message.remove_reaction("⏳", client.user)
        await message.add_reaction("❌")
        await message.reply("Timed out after 5 minutes.")
        if logs:
            await logs.send(f"[#{channel_type}] `{instruction[:100]}` → timed out")

    except Exception as e:
        await message.remove_reaction("⏳", client.user)
        await message.add_reaction("❌")
        await message.reply(f"Error: {e}")
        if logs:
            await logs.send(f"[#{channel_type}] `{instruction[:100]}` → error: {e}")


client.run(TOKEN)
