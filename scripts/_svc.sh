systemctl status pwony --no-pager -l | head -30
echo "--- logs"
sudo -n journalctl -u pwony -n 40 --no-pager
