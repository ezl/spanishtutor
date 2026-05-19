#!/bin/bash
# Run from your local machine: ./scripts/bootstrap.sh YOUR_SERVER_IP
# Sets up a fresh Hetzner server from scratch in one step.

set -e

SERVER_IP=$1
KEY="$HOME/.ssh/id_ed25519"
GITHUB_REPO="YOURUSERNAME/spanishtutor"

if [ -z "$SERVER_IP" ]; then
  echo "Usage: ./scripts/bootstrap.sh YOUR_SERVER_IP"
  exit 1
fi

echo "Bootstrapping $SERVER_IP..."

ssh -i $KEY root@$SERVER_IP bash << 'ENDSSH'
set -e

# System
apt update && apt upgrade -y

# Dependencies
apt install -y python3 python3-pip python3-venv git curl

# Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

# mise
curl -fsSL https://mise.run | sh
echo 'eval "$(/root/.local/bin/mise activate bash)"' >> ~/.bashrc
echo 'export PATH=/root/.local/bin:$PATH' >> ~/.bashrc

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

echo "Dependencies installed."
ENDSSH

# Clone repo
ssh -i $KEY root@$SERVER_IP "git clone https://github.com/$GITHUB_REPO /root/spanishtutor || (cd /root/spanishtutor && git pull)"

# Copy .env.example as .env
ssh -i $KEY root@$SERVER_IP "cp /root/spanishtutor/.env.example /root/spanishtutor/.env && chmod 600 /root/spanishtutor/.env"

echo ""
echo "Done. Next steps:"
echo "  1. Fill in secrets: ssh -i $KEY root@$SERVER_IP then nano /root/spanishtutor/.env"
echo "  2. Authenticate Claude Code: ssh -i $KEY root@$SERVER_IP then claude"
echo "  3. Start the agent: mise run start"
