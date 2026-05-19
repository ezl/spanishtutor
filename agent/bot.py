import discord
import asyncio
import subprocess
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

TOKEN = os.getenv("DISCORD_DEV_BOT_TOKEN")
REPO_PATH = os.getenv("REPO_PATH", "/root/spanishtutor")
TMUX_SESSION = "claude-agent"
LOG_FILE = "/tmp/claude-output.log"

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

# Queue to serialize instructions — one at a time
queue = asyncio.Queue()
busy = False


def tmux(cmd):
    subprocess.run(["tmux"] + cmd, check=True)


def capture_pane() -> str:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", TMUX_SESSION, "-p"],
        capture_output=True, text=True
    )
    return result.stdout


def session_exists() -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True
    )
    return result.returncode == 0


def ensure_claude_session():
    """Start Claude Code in a tmux session if not already running."""
    if session_exists():
        return
    Path(LOG_FILE).write_text("")
    tmux(["new-session", "-d", "-s", TMUX_SESSION, "-c", REPO_PATH])
    # Pipe all output to log file
    tmux(["pipe-pane", "-t", TMUX_SESSION, f"cat >> {LOG_FILE}"])
    # Start Claude Code
    tmux(["send-keys", "-t", TMUX_SESSION, "claude", "Enter"])
    time.sleep(5)  # Wait for Claude to authenticate and show prompt


async def send_to_claude(instruction: str, timeout: int = 300) -> str:
    """Send instruction to persistent Claude session, wait for response."""
    ensure_claude_session()

    # Snapshot current log size so we only capture new output
    log_path = Path(LOG_FILE)
    start_size = log_path.stat().st_size if log_path.exists() else 0

    # Send instruction
    tmux(["send-keys", "-t", TMUX_SESSION, instruction, "Enter"])

    # Wait for output to appear then stabilize
    await asyncio.sleep(3)
    deadline = time.time() + timeout
    last_size = start_size
    stable_ticks = 0

    while time.time() < deadline:
        await asyncio.sleep(2)
        current_size = log_path.stat().st_size if log_path.exists() else 0
        if current_size == last_size:
            stable_ticks += 1
            if stable_ticks >= 4:  # 8 seconds of no new output = done
                break
        else:
            stable_ticks = 0
            last_size = current_size

    # Return only new output since we sent the instruction
    if log_path.exists():
        content = log_path.read_text()
        new_output = content[start_size:].strip()
        return new_output or "Done (no output)."
    return "Done."


def chunk(text: str, size: int = 1900):
    return [text[i:i+size] for i in range(0, len(text), size)]


async def process_queue():
    """Process instructions one at a time from the queue."""
    while True:
        message, instruction, channel_type = await queue.get()
        logs = client.get_channel(LOGS_CHANNEL_ID)
        try:
            result = await asyncio.wait_for(
                send_to_claude(instruction), timeout=300
            )
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
        await message.reply("Queued — Claude is busy with a previous instruction.")

    await queue.put((message, instruction, channel_type))


client.run(TOKEN)
