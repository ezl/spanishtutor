#!/bin/bash
# Spanish Tutor — Interactive Setup
# Run this once on your local machine to set everything up from scratch.
# Takes ~10 minutes. You'll be prompted at each manual step.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()     { echo -e "${GREEN}✓ $1${NC}"; }
info()   { echo -e "${YELLOW}→ $1${NC}"; }
header() { echo -e "\n${BOLD}$1${NC}"; echo "----------------------------------------"; }
err()    { echo -e "${RED}✗ $1${NC}"; exit 1; }
ask()    { echo -e "${BOLD}$1${NC}"; read -r "$2"; }

CONFIG_FILE=".setup-config"

save() { echo "$1=$2" >> $CONFIG_FILE; }
load() { grep "^$1=" $CONFIG_FILE 2>/dev/null | cut -d= -f2-; }

# Load existing progress
[ -f $CONFIG_FILE ] && source $CONFIG_FILE

echo ""
echo -e "${BOLD}================================================${NC}"
echo -e "${BOLD}  Spanish Tutor — Setup Wizard${NC}"
echo -e "${BOLD}================================================${NC}"
echo ""
echo "This script will walk you through setting up:"
echo "  1. SSH key"
echo "  2. Hetzner VPS"
echo "  3. GitHub repository"
echo "  4. Discord dev server + bot"
echo "  5. Deploying the agent"
echo ""
echo "You can re-run this script at any time — it resumes from where you left off."
echo ""
read -rp "Press Enter to begin..."

# ============================================================
header "STEP 1: SSH Key"
# ============================================================

if [ -f ~/.ssh/id_ed25519.pub ]; then
    ok "SSH key found at ~/.ssh/id_ed25519.pub"
else
    info "No SSH key found. Generating one now..."
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
    ok "SSH key generated"
fi

echo ""
echo "Your public key (you'll need this for Hetzner):"
echo ""
cat ~/.ssh/id_ed25519.pub
echo ""

# ============================================================
header "STEP 2: Hetzner VPS"
# ============================================================

if [ -n "$SERVER_IP" ]; then
    ok "Server IP already set: $SERVER_IP"
else
    info "Create your Hetzner server now:"
    echo ""
    echo "  1. Go to: https://console.hetzner.com"
    echo "  2. Create account if needed, then open Default project"
    echo "  3. Click '+ Create Server'"
    echo "  4. Choose:"
    echo "       Type:     Shared → Regular Performance"
    echo "       Size:     CPX11 or CPX22"
    echo "       Location: us-west (Hillsboro, OR) — best availability for new accounts"
    echo "       Image:    Ubuntu 26.04"
    echo "       SSH Key:  paste the key shown above"
    echo "       Name:     spanishtutor-agent"
    echo "  5. Click 'Create & Buy Now'"
    echo "  6. Copy the server IP from the dashboard"
    echo ""
    ask "Paste your server IP here:" SERVER_IP
    save "SERVER_IP" "$SERVER_IP"
fi

info "Testing SSH connection to $SERVER_IP..."
if ssh -i ~/.ssh/id_ed25519 -o ConnectTimeout=10 -o StrictHostKeyChecking=no root@$SERVER_IP "echo ok" &>/dev/null; then
    ok "SSH connection successful"
else
    err "Cannot connect to $SERVER_IP. Check the IP and try again."
fi

# ============================================================
header "STEP 3: Bootstrap the Server"
# ============================================================

if [ "$(load SERVER_BOOTSTRAPPED)" = "true" ]; then
    ok "Server already bootstrapped"
else
    info "Installing dependencies on the server (this takes ~2 minutes)..."
    ssh -i ~/.ssh/id_ed25519 root@$SERVER_IP bash << 'ENDSSH'
set -e
apt update -qq && apt upgrade -y -qq
apt install -y python3 python3-pip python3-venv git curl tmux -qq
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt install -y nodejs -qq
curl -fsSL https://mise.run | sh > /dev/null 2>&1
echo 'eval "$(/root/.local/bin/mise activate bash)"' >> ~/.bashrc
echo 'export PATH=/root/.local/bin:$PATH' >> ~/.bashrc
npm install -g @anthropic-ai/claude-code > /dev/null 2>&1
echo "Bootstrap complete"
ENDSSH
    save "SERVER_BOOTSTRAPPED" "true"
    ok "Server bootstrapped"
fi

# ============================================================
header "STEP 4: GitHub Repository"
# ============================================================

if [ -n "$GITHUB_REPO" ]; then
    ok "GitHub repo already set: $GITHUB_REPO"
else
    info "Create your GitHub repository:"
    echo ""
    echo "  1. Go to: https://github.com/new"
    echo "  2. Name it: spanishtutor"
    echo "  3. Set to Private"
    echo "  4. Leave everything else unchecked"
    echo "  5. Click 'Create repository'"
    echo ""
    ask "Paste your GitHub repo URL (e.g. https://github.com/yourname/spanishtutor):" GITHUB_REPO
    save "GITHUB_REPO" "$GITHUB_REPO"
fi

# Initialize and push if not already done
if [ "$(load GITHUB_PUSHED)" != "true" ]; then
    if [ ! -d .git ]; then
        git init
        git branch -M main
    fi
    if ! git remote get-url origin &>/dev/null; then
        git remote add origin "$GITHUB_REPO.git"
    fi
    git add .
    git commit -m "initial setup" --allow-empty
    git push -u origin main
    save "GITHUB_PUSHED" "true"
    ok "Code pushed to GitHub"
else
    ok "GitHub already set up"
fi

# Clone repo on server
ssh -i ~/.ssh/id_ed25519 root@$SERVER_IP bash << ENDSSH
if [ -d /root/spanishtutor/.git ]; then
    cd /root/spanishtutor && git pull origin main -q
else
    git clone $GITHUB_REPO.git /root/spanishtutor -q
fi
ENDSSH
ok "Server repo synced"

# ============================================================
header "STEP 5: Discord Dev Server"
# ============================================================

if [ "$(load DISCORD_DEV_SERVER_DONE)" = "true" ]; then
    ok "Discord dev server already configured"
else
    info "Create your Discord dev server:"
    echo ""
    echo "  1. Open Discord"
    echo "  2. Click '+' in the left sidebar → 'Create My Own' → 'For me and my friends'"
    echo "  3. Name it: Spanish Tutor Dev"
    echo "  4. Create these 5 text channels:"
    echo "       #features  #dev  #deploy  #bugs  #logs"
    echo "  5. Go to: User Settings → Advanced → enable Developer Mode"
    echo "  6. Right-click each channel → Copy Channel ID"
    echo ""
    ask "Paste #features channel ID:" DISCORD_DEV_FEATURES_CHANNEL_ID
    ask "Paste #dev channel ID:" DISCORD_DEV_CHANNEL_ID
    ask "Paste #deploy channel ID:" DISCORD_DEV_DEPLOY_CHANNEL_ID
    ask "Paste #bugs channel ID:" DISCORD_DEV_BUGS_CHANNEL_ID
    ask "Paste #logs channel ID:" DISCORD_DEV_LOGS_CHANNEL_ID

    save "DISCORD_DEV_FEATURES_CHANNEL_ID" "$DISCORD_DEV_FEATURES_CHANNEL_ID"
    save "DISCORD_DEV_CHANNEL_ID" "$DISCORD_DEV_CHANNEL_ID"
    save "DISCORD_DEV_DEPLOY_CHANNEL_ID" "$DISCORD_DEV_DEPLOY_CHANNEL_ID"
    save "DISCORD_DEV_BUGS_CHANNEL_ID" "$DISCORD_DEV_BUGS_CHANNEL_ID"
    save "DISCORD_DEV_LOGS_CHANNEL_ID" "$DISCORD_DEV_LOGS_CHANNEL_ID"
    save "DISCORD_DEV_SERVER_DONE" "true"
    ok "Discord channel IDs saved"
fi

# ============================================================
header "STEP 6: Discord Bot"
# ============================================================

if [ -n "$DISCORD_DEV_BOT_TOKEN" ]; then
    ok "Discord dev bot token already set"
else
    info "Create your Discord bot:"
    echo ""
    echo "  1. Go to: https://discord.com/developers/applications"
    echo "  2. Click 'New Application' → name it 'Spanish Tutor Dev'"
    echo "  3. Click 'Bot' in the left sidebar"
    echo "  4. Click 'Reset Token' → copy the token"
    echo "  5. Enable 'Message Content Intent' under Privileged Gateway Intents"
    echo "  6. Go to OAuth2 → URL Generator"
    echo "     Scopes: bot"
    echo "     Permissions: View Channels, Send Messages, Read Message History"
    echo "  7. Copy the generated URL → open in browser → add bot to 'Spanish Tutor Dev'"
    echo ""
    ask "Paste your bot token:" DISCORD_DEV_BOT_TOKEN
    save "DISCORD_DEV_BOT_TOKEN" "$DISCORD_DEV_BOT_TOKEN"
    ok "Bot token saved"
fi

# ============================================================
header "STEP 7: Anthropic API Key"
# ============================================================

if [ -n "$ANTHROPIC_API_KEY" ]; then
    ok "Anthropic API key already set"
else
    info "Get your Anthropic API key:"
    echo ""
    echo "  1. Go to: https://console.anthropic.com"
    echo "  2. API Keys → Create Key"
    echo "  3. Copy the key"
    echo ""
    ask "Paste your Anthropic API key:" ANTHROPIC_API_KEY
    save "ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"
    ok "Anthropic API key saved"
fi

# ============================================================
header "STEP 8: Write Secrets to Server"
# ============================================================

info "Writing all secrets to server .env..."
ssh -i ~/.ssh/id_ed25519 root@$SERVER_IP bash << ENDSSH
cat > /root/spanishtutor/.env << 'EOF'
DISCORD_DEV_BOT_TOKEN=$DISCORD_DEV_BOT_TOKEN
DISCORD_DEV_FEATURES_CHANNEL_ID=$DISCORD_DEV_FEATURES_CHANNEL_ID
DISCORD_DEV_CHANNEL_ID=$DISCORD_DEV_CHANNEL_ID
DISCORD_DEV_DEPLOY_CHANNEL_ID=$DISCORD_DEV_DEPLOY_CHANNEL_ID
DISCORD_DEV_BUGS_CHANNEL_ID=$DISCORD_DEV_BUGS_CHANNEL_ID
DISCORD_DEV_LOGS_CHANNEL_ID=$DISCORD_DEV_LOGS_CHANNEL_ID
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
DATABASE_URL=
GITHUB_TOKEN=
GITHUB_REPO=$GITHUB_REPO
REPO_PATH=/root/spanishtutor
EOF
chmod 600 /root/spanishtutor/.env
ENDSSH
ok "Secrets written to server"

# ============================================================
header "STEP 9: Install Dependencies + Start Agent"
# ============================================================

info "Installing Python dependencies on server..."
ssh -i ~/.ssh/id_ed25519 root@$SERVER_IP bash << 'ENDSSH'
python3 -m venv /root/spanishtutor/venv
/root/spanishtutor/venv/bin/pip install -q -r /root/spanishtutor/agent/requirements.txt
cp /root/spanishtutor/agent/spanishtutor-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable spanishtutor-agent
systemctl restart spanishtutor-agent
ENDSSH
ok "Agent deployed and running"

# ============================================================
header "STEP 10: Authenticate Claude Code"
# ============================================================

echo ""
info "One manual step remaining: authenticate Claude Code on the server."
echo ""
echo "  Run: mise run claude"
echo "  This opens the Claude Code session in tmux."
echo "  Complete the browser auth flow, then press Ctrl+B then D to detach."
echo ""
echo "After authenticating, your agent is fully operational."
echo "Test it by sending a message in #dev on your Discord server."
echo ""

# ============================================================
echo ""
echo -e "${BOLD}================================================${NC}"
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo -e "${BOLD}================================================${NC}"
echo ""
echo "Useful commands:"
echo "  mise run claude     — attach to Claude session (first time: authenticate)"
echo "  mise run logs       — tail agent logs"
echo "  mise run status     — check if agent is running"
echo "  mise run restart    — restart agent"
echo "  mise run secrets    — edit secrets on server"
echo ""
echo "Your Discord dev server is ready. Send a message in #dev to test."
echo ""
