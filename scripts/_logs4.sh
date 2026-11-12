sudo -n journalctl -u ai-classroom --since "3 min ago" --no-pager | grep -E 'realtime|WebSocket' | tail -20
