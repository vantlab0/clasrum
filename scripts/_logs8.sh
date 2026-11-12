sudo -n journalctl -u ai-classroom --since "4 min ago" --no-pager | grep -A 30 "Traceback\|ERROR" | tail -60
