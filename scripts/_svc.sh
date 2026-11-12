systemctl status ai-classroom --no-pager -l | head -30
echo "--- logs"
sudo -n journalctl -u ai-classroom -n 40 --no-pager
