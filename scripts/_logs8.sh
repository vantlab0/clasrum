sudo -n journalctl -u pwony --since "4 min ago" --no-pager | grep -A 30 "Traceback\|ERROR" | tail -60
