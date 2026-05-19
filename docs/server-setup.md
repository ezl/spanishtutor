# Server Setup Guide

Estimated time: 10 minutes. For a completely fresh setup, just run `./setup.sh` — it walks through every step interactively. This doc is the manual reference for what that script does.

---

## Prerequisites

- Hetzner account (hetzner.com → Cloud)
- SSH key at `~/.ssh/id_ed25519` — if missing, run `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`
- GitHub account
- Discord account
- Anthropic API key (console.anthropic.com)

---

## Step 1: Create Hetzner Server

1. Log into console.hetzner.com
2. Select project → **+ Create Server**
3. Settings:
   - **Type**: Shared Resources → Regular Performance
   - **Size**: CPX11 (2 vCPU, 2GB RAM) or CPX22 (2 vCPU, 4GB RAM)
   - **Location**: us-west (Hillsboro, OR) — us-east often has availability issues for new accounts
   - **Image**: Ubuntu 26.04
   - **Networking**: Public IPv4 + IPv6, no private network
   - **SSH Key**: paste contents of `~/.ssh/id_ed25519.pub`
   - **Volumes / Firewalls / Backups**: none
   - **Name**: `spanishtutor-agent`
4. Click **Create & Buy Now**
5. Copy the server IP from the dashboard

---

## Step 2: Bootstrap the Server

From your local machine:

```bash
./scripts/bootstrap.sh YOUR_SERVER_IP
```

This installs: Python 3, Node.js 20, git, curl, tmux, mise, Claude Code CLI. Verifies each at the end.

---

## Step 3: Fill In Secrets

Use the mise command (runs nano on the server remotely):

```bash
mise run secrets
```

Required values — see `.env.example` for where to get each:
- `DISCORD_DEV_BOT_TOKEN`
- `DISCORD_DEV_FEATURES_CHANNEL_ID`
- `DISCORD_DEV_CHANNEL_ID`
- `DISCORD_DEV_DEPLOY_CHANNEL_ID`
- `DISCORD_DEV_BUGS_CHANNEL_ID`
- `DISCORD_DEV_LOGS_CHANNEL_ID`
- `ANTHROPIC_API_KEY`
- `DATABASE_URL`
- `GITHUB_TOKEN`
- `GITHUB_REPO`

---

## Step 4: Install Python Dependencies + Start Agent

```bash
ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
python3 -m venv /root/spanishtutor/venv
/root/spanishtutor/venv/bin/pip install -r /root/spanishtutor/agent/requirements.txt
cp /root/spanishtutor/agent/spanishtutor-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable spanishtutor-agent
systemctl start spanishtutor-agent
```

Verify:
```bash
mise run status
```

---

## Step 5: Authenticate Claude Code

This is a one-time manual step. SSH into the server and attach to the tmux session:

```bash
# From your local machine (note: use TERM override for Ghostty users)
TERM=xterm-256color ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
```

Once on the server:
```bash
tmux attach -t claude-agent
# or if already in a tmux session:
tmux switch-client -t claude-agent
```

Then start Claude Code with permissions bypassed (required for unattended operation):
```bash
claude --dangerously-skip-permissions
```

Complete the browser auth flow. Then detach from tmux with `Ctrl+B` then `D`.

**Note:** Authenticating on the server does not affect your local Claude Code session — they use separate tokens.

---

## Step 6: Verify End-to-End

Send a message in `#dev` on your Discord dev server. You should see:
- ⏳ reaction appears on your message
- Response posted as a reply
- ✅ reaction replaces ⏳
- Confirmation in `#logs`

---

## Common Commands

```bash
mise run claude       # Attach to Claude tmux session on server
mise run logs         # Tail agent logs
mise run status       # Check if agent is running
mise run restart      # Restart agent
mise run update       # Pull latest code + restart
mise run secrets      # Edit .env on server
mise run secrets-show # List secret keys (no values)
```

---

## Troubleshooting

**`command not found: claude` on server**
```bash
npm install -g @anthropic-ai/claude-code
```

**`missing or unsuitable terminal: xterm-ghostty`**
Prefix your SSH command with `TERM=xterm-256color`

**`sessions should be nested with care`**
You're already in a tmux session. Use `tmux switch-client -t claude-agent` instead of attach.

**Bot shows offline in Discord**
```bash
mise run status   # check if service is running
mise run logs     # check for errors
```
