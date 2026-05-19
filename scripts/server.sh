#!/bin/bash
# Convenience script for common server operations
SERVER="root@5.78.211.212"
KEY="~/.ssh/id_ed25519"

case "$1" in
  ssh)
    ssh -i $KEY $SERVER
    ;;
  logs)
    ssh -i $KEY $SERVER "journalctl -u spanishtutor-agent -f"
    ;;
  status)
    ssh -i $KEY $SERVER "systemctl status spanishtutor-agent"
    ;;
  restart)
    ssh -i $KEY $SERVER "systemctl restart spanishtutor-agent"
    ;;
  update)
    ssh -i $KEY $SERVER "cd /root/spanishtutor && git pull origin main && systemctl restart spanishtutor-agent"
    ;;
  *)
    echo "Usage: ./scripts/server.sh [ssh|logs|status|restart|update]"
    ;;
esac
