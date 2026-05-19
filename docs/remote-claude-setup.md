# Remote Claude Code Agent Setup

A generalized guide for running a persistent Claude Code instance on a Hetzner VPS, controlled via Discord. Works for any GitHub repo.

Estimated setup time: 15 minutes.

---

## Overview

```
You (Discord, phone or desktop)
    → message in #dev / #deploy / #bugs / #features
    → Discord bot (running on VPS as systemd service)
    → sends instruction to Claude Code (running in tmux as non-root user)
    → Claude edits code, runs commands, pushes to GitHub
    → bot captures output and replies in Discord
    → GitHub push triggers auto-deploy (Railway/Render)
```

---

## Prerequisites

On your local machine:
- SSH key at `~/.ssh/id_ed25519` (run `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519` if missing)
- Git installed
- mise installed (`curl https://mise.run | sh`)

Accounts needed:
- Hetzner Cloud (hetzner.com)
- GitHub
- Discord
- Anthropic (console.anthropic.com) — for Claude API key

---

## Part 1: Hetzner Server

### 1.1 Create Server

1. Log into console.hetzner.com
2. Open or create a project → **+ Create Server**
3. Settings:
   - **Type**: Shared Resources → Regular Performance
   - **Size**: CPX11 (2 vCPU, 2GB RAM, ~€6.50/mo) — sufficient for one Claude agent
   - **Location**: us-west (Hillsboro, OR) — new accounts often have availability issues in us-east
   - **Image**: Ubuntu 26.04
   - **Networking**: Public IPv4 ✓, Public IPv6 ✓, Private networks ✗
   - **SSH Key**: paste contents of `~/.ssh/id_ed25519.pub`
   - **Volumes / Firewalls / Backups / Placement groups**: none
   - **Name**: something meaningful, e.g. `myproject-agent`
4. Click **Create & Buy Now**
5. Note the server IP

### 1.2 Verify Connection

```bash
ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
```

---

## Part 2: Bootstrap the Server

Run from your local machine:

```bash
./scripts/bootstrap.sh YOUR_SERVER_IP
```

Or manually on the server:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl tmux

# Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

# mise
curl -fsSL https://mise.run | sh
echo 'eval "$(/root/.local/bin/mise activate bash)"' >> ~/.bashrc
echo 'export PATH=/root/.local/bin:$PATH' >> ~/.bashrc

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Verify
node --version && python3 --version && claude --version
```

---

## Part 3: Create Non-Root User

**Critical:** Claude Code blocks `--dangerously-skip-permissions` when running as root. You must run it as a non-root user.

```bash
# On the server as root:
useradd -m -s /bin/bash claude

# Copy SSH authorized keys so you can SSH as this user if needed
mkdir -p /home/claude/.ssh
cp /root/.ssh/authorized_keys /home/claude/.ssh/
chown -R claude:claude /home/claude/.ssh
chmod 700 /home/claude/.ssh && chmod 600 /home/claude/.ssh/authorized_keys

# Allow claude user to restart the agent service without password
echo 'claude ALL=(ALL) NOPASSWD: /bin/systemctl restart spanishtutor-agent, /bin/systemctl status spanishtutor-agent, /bin/systemctl stop spanishtutor-agent' >> /etc/sudoers
```

---

## Part 4: Clone the Repo

```bash
# As root, clone into claude user's home
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git /home/claude/project
chown -R claude:claude /home/claude/project

# Create Python virtualenv
su - claude -c 'python3 -m venv /home/claude/project/venv'
su - claude -c '/home/claude/project/venv/bin/pip install -r /home/claude/project/agent/requirements.txt'
```

---

## Part 5: Discord Setup

### 5.1 Create Discord Server

1. Open Discord → **+** → Create My Own → For me and my friends
2. Name it (e.g. "My Project Dev")
3. Create text channels: `#features`, `#dev`, `#deploy`, `#bugs`, `#logs`
4. Enable Developer Mode: User Settings → Advanced → Developer Mode
5. Right-click each channel → **Copy Channel ID** — save all 5

### 5.2 Create Discord Bot

1. Go to discord.com/developers/applications → **New Application**
2. Name it (e.g. "MyProject Dev Bot")
3. Left sidebar → **Bot**
4. **Reset Token** → copy and save the token
5. Enable **Message Content Intent** under Privileged Gateway Intents → Save Changes
6. Left sidebar → **OAuth2** → URL Generator
   - Scopes: `bot`
   - Bot Permissions: View Channels, Send Messages, Read Message History
7. Copy the generated URL → open in browser → add bot to your server

---

## Part 6: Configure Secrets

Copy `.env.example` to `.env` on the server and fill in values:

```bash
cp /home/claude/project/.env.example /home/claude/project/.env
chmod 600 /home/claude/project/.env
nano /home/claude/project/.env
```

Required values:

```
DISCORD_DEV_BOT_TOKEN=          # From Discord Developer Portal → Bot → Token
DISCORD_DEV_FEATURES_CHANNEL_ID=
DISCORD_DEV_CHANNEL_ID=
DISCORD_DEV_DEPLOY_CHANNEL_ID=
DISCORD_DEV_BUGS_CHANNEL_ID=
DISCORD_DEV_LOGS_CHANNEL_ID=
ANTHROPIC_API_KEY=              # From console.anthropic.com → API Keys
GITHUB_TOKEN=                   # GitHub → Settings → Developer Settings → Fine-grained tokens
GITHUB_REPO=                    # e.g. username/reponame
REPO_PATH=/home/claude/project
```

---

## Part 7: systemd Service

Create `/etc/systemd/system/myproject-agent.service`:

```ini
[Unit]
Description=My Project Dev Agent Bot
After=network.target

[Service]
Type=simple
User=claude
WorkingDirectory=/home/claude/project
ExecStart=/home/claude/project/venv/bin/python3 /home/claude/project/agent/bot.py
Restart=always
RestartSec=5
Environment=REPO_PATH=/home/claude/project

[Install]
WantedBy=multi-user.target
```

Then:

```bash
systemctl daemon-reload
systemctl enable myproject-agent
systemctl start myproject-agent
systemctl status myproject-agent
```

---

## Part 8: Authenticate Claude Code

This must be done manually once per user account. It does **not** affect your local Claude Code session.

### 8.1 Start Claude in tmux as the claude user

```bash
# As root on the server:
su - claude -c 'tmux new-session -d -s claude-agent -c /home/claude/project'
su - claude -c 'tmux send-keys -t claude-agent "claude --dangerously-skip-permissions" Enter'
```

### 8.2 Attach to the session

**From your local machine** (note TERM override for Ghostty users):

```bash
TERM=xterm-256color ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
```

**Once on the server:**

```bash
# If not already in a tmux session:
su - claude
tmux attach -t claude-agent

# If already inside a tmux session (you'll see a status bar at the bottom):
su - claude
tmux switch-client -t claude-agent
```

### 8.3 Complete auth flow

- Claude will show a theme selection prompt → pick any (e.g. `1`)
- Then show a login option prompt → select **1** (Claude.ai)
- A URL will appear → open in browser → authenticate
- Return to terminal, wait for Claude prompt to appear
- Confirm you see: `bypass permissions on` in the footer

### 8.4 Detach from tmux

Press `Ctrl+B` then `D`

---

## Part 9: Verify End-to-End

Send a message in `#dev` on your Discord server:

```
what files are in this repo?
```

Expected:
1. ⏳ reaction appears on your message immediately
2. ~15-30 seconds later, Claude's response posted as a reply
3. ✅ reaction replaces ⏳
4. Confirmation logged in `#logs`

---

## Restart Procedure

If the server reboots or the Claude session dies:

```bash
# Check if bot service is running
systemctl status myproject-agent

# Check if Claude tmux session exists
su - claude -c 'tmux list-sessions'

# If session is gone, recreate it:
su - claude -c 'tmux new-session -d -s claude-agent -c /home/claude/project'
su - claude -c 'tmux send-keys -t claude-agent "claude --dangerously-skip-permissions" Enter'
# Then attach and re-auth if needed (auth usually persists between restarts)

# Restart the bot service
systemctl restart myproject-agent
```

**Note:** Claude Code auth token persists on disk, so re-authentication after a reboot is usually not needed — only after creating a fresh user account.

---

## Pointing to a Different GitHub Repo

1. Update `GITHUB_REPO` and `REPO_PATH` in `/home/claude/project/.env`
2. Clone the new repo: `su - claude -c 'git clone https://github.com/NEW/REPO.git /home/claude/newproject'`
3. Install its dependencies
4. Update the systemd service `WorkingDirectory` and `REPO_PATH`
5. Restart: `systemctl daemon-reload && systemctl restart myproject-agent`
6. Kill and recreate the Claude tmux session pointing to the new directory

---

## Common Commands

```bash
# From local machine (via mise):
mise run claude       # Attach to Claude tmux session
mise run logs         # Tail bot logs
mise run status       # Check bot service status
mise run restart      # Restart bot service
mise run update       # git pull + restart
mise run secrets      # Edit .env on server
mise run secrets-show # List secret keys (no values shown)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: claude` | `npm install -g @anthropic-ai/claude-code` |
| `--dangerously-skip-permissions cannot be used with root` | Run claude as a non-root user (see Part 3) |
| `missing or unsuitable terminal: xterm-ghostty` | Prefix SSH with `TERM=xterm-256color` |
| `sessions should be nested with care` | Already in tmux — use `tmux switch-client -t claude-agent` not `attach` |
| Bot replies "Done (no output)" | Claude session not running or stuck on a prompt — attach and check |
| Bot offline in Discord | `systemctl status myproject-agent` then `mise run logs` |
| Instructions going to bash shell | Claude session died — recreate it (see Restart Procedure) |
| Auth prompt appears in Claude session | Complete browser auth flow, then detach |
