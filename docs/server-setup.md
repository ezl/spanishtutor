# Server Setup — Spanish Tutor

This is the project-specific setup guide. For the full generalized reference (any repo, any restart scenario), see [remote-claude-setup.md](remote-claude-setup.md).

---

## Quick Setup (Fresh Server)

```bash
./setup.sh
```

The interactive wizard covers everything. Resume at any point — it saves progress.

---

## Project-Specific Details

| Setting | Value |
|---|---|
| Server | Hetzner CPX11, us-west (Hillsboro OR) |
| Server IP | 5.78.211.212 |
| OS user for Claude | `claude` |
| Repo path on server | `/home/claude/spanishtutor` |
| systemd service | `spanishtutor-agent` |
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
mise run status

# If bot is down:
mise run restart
```

---

## Common Commands

```bash
mise run claude       # Attach to Claude tmux session on server
mise run logs         # Tail agent logs
mise run status       # Check if agent service is running
mise run restart      # Restart agent service
mise run update       # git pull + restart
mise run secrets      # Edit .env on server
mise run secrets-show # List secret keys (no values)
```

---

## Secrets Location

All secrets live in `/home/claude/spanishtutor/.env` on the server. See `.env.example` for the full list and where to get each value.
