uptime
echo "--- services"
systemctl is-active ai-classroom nginx
echo "--- listeners"
sudo -n ss -tlnp | grep -E ':(80|8000|10000|10001) ' || true
echo "--- pm2"
sudo -n env PATH=$PATH:/usr/bin pm2 list 2>&1 | tail -8
