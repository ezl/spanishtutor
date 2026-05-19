import discord
import asyncio
import subprocess
import os
import re
import time
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

TOKEN = os.getenv("DISCORD_DEV_BOT_TOKEN")
REPO_PATH = os.getenv("REPO_PATH", "/root/spanishtutor")
TMUX_SESSION = "claude-agent"

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
queue = asyncio.Queue()


def strip_ansi(text: str) -> str:
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)


def tmux_run(cmd: list) -> str:
    result = subprocess.run(["tmux"] + cmd, capture_output=True, text=True)
    return result.stdout


def capture_pane() -> str:
    raw = tmux_run(["capture-pane", "-t", TMUX_SESSION, "-p", "-e"])
    return strip_ansi(raw)


def session_exists() -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", TMUX_SESSION], capture_output=True)
    return result.returncode == 0


def ensure_claude_session():
    if session_exists():
        return
    subprocess.run(["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-c", REPO_PATH])
    subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION,
                    "claude --dangerously-skip-permissions", "Enter"])
    time.sleep(5)


async def send_to_claude(instruction: str, timeout: int = 300) -> str:
    ensure_claude_session()

    # Snapshot pane before sending
    before = capture_pane()

    # Send instruction
    subprocess.run(["tmux", "send-keys", "-t", TMUX_SESSION, instruction, "Enter"])

    # Wait for output to appear and stabilize
    await asyncio.sleep(5)
    deadline = time.time() + timeout
    last = capture_pane()
    stable = 0

    while time.time() < deadline:
        await asyncio.sleep(3)
        current = capture_pane()
        if current == last:
            stable += 1
            if stable >= 3:  # 9 seconds stable = done
                break
        else:
            stable = 0
            last = current

    # Return only what's new since we sent the instruction
    new_content = last[len(before):].strip()
    return new_content or last.strip() or "Done (no output)."


def chunk(text: str, size: int = 1900):
    return [text[i:i+size] for i in range(0, len(text), size)]


async def process_queue():
    while True:
        message, instruction, channel_type = await queue.get()
        logs = client.get_channel(LOGS_CHANNEL_ID)
        try:
            result = await asyncio.wait_for(send_to_claude(instruction), timeout=300)
            await message.remove_reaction("⏳", client.user)
            await message.add_reaction("✅")
            for part in chunk(result):
                await message.reply(f"```\n{part}\n```")
            if logs:
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
        finally:
            queue.task_done()


@client.event
async def on_ready():
    logs = client.get_channel(LOGS_CHANNEL_ID)
    print(f"Agent online as {client.user}")
    ensure_claude_session()
    asyncio.create_task(process_queue())
    if logs:
        await logs.send("Agent online. Claude session ready.")


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.channel.id not in ACTION_CHANNELS:
        return
    instruction = message.content.strip()
    if not instruction:
        return
    channel_type = ACTION_CHANNELS[message.channel.id]
    await message.add_reaction("⏳")
    if not queue.empty():
        await message.reply("Queued — Claude is finishing a previous instruction.")
    await queue.put((message, instruction, channel_type))


client.run(TOKEN)
