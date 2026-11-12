sudo -n journalctl -u ai-classroom -n 60 --no-pager | tail -40
echo "--- nginx error log"
sudo -n tail -10 /var/log/nginx/error.log
