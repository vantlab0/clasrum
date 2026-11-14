sudo -n journalctl -u pwony --since "3 min ago" --no-pager | grep -E 'realtime|WebSocket' | tail -20
