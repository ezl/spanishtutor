# Server Setup — Spanish Tutor

The remote Claude agent infrastructure lives in a **separate repo**: [discord-claude-agent](https://github.com/ezl/discord-claude-agent). That repo contains the bot, setup scripts, and full setup guide.

This doc covers project-specific server configuration only.

---

## Server Details

| Setting | Value |
|---|---|
| Server | Hetzner CPX11, us-west (Hillsboro OR) |
| Server IP | 5.78.211.212 |
| OS user for Claude | `claude` |
| App repo path | `/home/claude/spanishtutor` |
| Agent repo path | `/home/claude/discord-claude-agent` |
| systemd service | `discord-claude-agent` |
| tmux session | `claude-agent` |
| Discord learning server | Spanish Tutor (learning bot) |
| Discord dev server | Spanish Tutor Dev (agent bot) |

---

## Restart After Reboot

```bash
# Check if Claude session is alive
ssh -i ~/.ssh/id_ed25519 root@5.78.211.212 "su - claude -c 'tmux list-sessions'"

# If session is gone, recreate it:
ssh -i ~/.ssh/id_ed25519 root@5.78.211.212 "su - claude -c 'tmux new-session -d -s claude-agent -c /home/claude/spanishtutor && tmux send-keys -t claude-agent \"claude --dangerously-skip-permissions\" Enter'"

# Check bot service
mise run agent-status

# If bot is down:
mise run agent-restart
```

---

## Common Commands

```bash
mise run claude          # Attach to Claude tmux session on server
mise run agent-logs      # Tail agent bot logs
mise run agent-status    # Check if agent service is running
mise run agent-restart   # Restart agent service
mise run secrets         # Edit app .env on server
mise run secrets-show    # List app secret keys (no values)
```

---

## Secrets

App secrets live in `/home/claude/spanishtutor/.env`.
Agent secrets live in `/home/claude/discord-claude-agent/.env`.

See `.env.example` in each repo for the full list and where to get each value.
