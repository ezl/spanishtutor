# Server Setup Guide

Estimated time: 10 minutes. Covers provisioning a new Hetzner VPS and getting it fully configured for the Spanish Tutor agent.

---

## Prerequisites

- Hetzner account (hetzner.com → Cloud)
- SSH key on your local machine (`~/.ssh/id_ed25519.pub`)
- GitHub repo already exists
- `.env` values ready to paste (see `.env.example`)

---

## Step 1: Create Hetzner Server

1. Log into console.hetzner.com
2. Select project → **+ Create Server**
3. Settings:
   - **Type**: Shared Resources → Regular Performance
   - **Size**: CPX11 (2 vCPU, 2GB RAM) or CPX22 (2 vCPU, 4GB RAM) — CPX22 recommended
   - **Location**: us-west (Hillsboro, OR) — us-east often has availability issues for new accounts
   - **Image**: Ubuntu 26.04
   - **Networking**: Public IPv4 + IPv6, no private network
   - **SSH Key**: paste contents of `~/.ssh/id_ed25519.pub`
   - **Volumes**: none
   - **Firewalls**: none
   - **Backups**: none
   - **Name**: `spanishtutor-agent`
4. Click **Create & Buy Now**
5. Copy the server IP from the dashboard

---

## Step 2: Connect and Verify

```bash
ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
```

---

## Step 3: Run Setup Script

From your local machine, run the bootstrap script:

```bash
./scripts/bootstrap.sh YOUR_SERVER_IP
```

This installs all dependencies, clones the repo, and configures the server in one step.

---

## Step 4: Fill In Secrets

SSH into the server and fill in the `.env` file:

```bash
ssh -i ~/.ssh/id_ed25519 root@YOUR_SERVER_IP
nano /root/spanishtutor/.env
```

Required values — see `.env.example` for where to get each one:
- `DISCORD_DEV_BOT_TOKEN`
- `DISCORD_TUTOR_BOT_TOKEN`
- All 9 channel IDs
- `ANTHROPIC_API_KEY`
- `DATABASE_URL`
- `GITHUB_TOKEN`
- `GITHUB_REPO`

---

## Step 5: Start the Agent

```bash
mise run start
```

Verify it's running:

```bash
mise run status
```

---

## Common Commands (after setup)

```bash
./scripts/server.sh ssh        # SSH into server
./scripts/server.sh logs       # Tail agent logs
./scripts/server.sh status     # Check if agent is running
./scripts/server.sh restart    # Restart agent
./scripts/server.sh update     # Pull latest code + restart
```
