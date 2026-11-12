echo "--- pm2 list"
sudo -n env PATH=$PATH:/usr/bin pm2 list 2>&1 | head -30
echo "--- pm2 jlist names"
sudo -n env PATH=$PATH:/usr/bin pm2 jlist 2>/dev/null | head -c 1500
echo
echo "--- env perms"
ls -l /opt/avelut-realtime-proxy/.env
echo "--- port 80 vs 10000 usage"
sudo -n ss -tlnp | grep -E ':(80|10000) '
