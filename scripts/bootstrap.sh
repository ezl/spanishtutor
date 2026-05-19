#!/bin/bash
# Run from your local machine: ./scripts/bootstrap.sh YOUR_SERVER_IP
# Sets up a fresh Hetzner server with all dependencies for a remote Claude Code agent.
# Repo-agnostic — works for any project. See docs/remote-claude-setup.md for full guide.

set -e

SERVER_IP=$1
KEY="$HOME/.ssh/id_ed25519"

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

# Verify
node --version
python3 --version
claude --version

echo "Dependencies installed."
ENDSSH

echo ""
echo "Bootstrap complete. Next steps:"
echo "  See docs/remote-claude-setup.md for full instructions, or run ./setup.sh"
echo ""
echo "  Manual steps remaining:"
echo "  1. Create non-root 'claude' user (see Part 3 of remote-claude-setup.md)"
echo "  2. Clone your repo into /home/claude/project"
echo "  3. Fill in /home/claude/project/.env"
echo "  4. Install and start the systemd service"
echo "  5. Authenticate Claude Code in tmux as the claude user"
