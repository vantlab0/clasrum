echo "--- processes"
ps aux | grep -Ei 'node|pm2' | grep -v grep || true
echo "--- README"
cat /opt/avelut-realtime-proxy/README.md 2>&1 | head -60
echo "--- env"
cat /opt/avelut-realtime-proxy/.env 2>&1 | sed 's/=.*/=<redacted>/'
echo "--- package.json"
cat /opt/avelut-realtime-proxy/package.json
echo "--- who owns 10000"
sudo -n ss -tlnp | grep ':10000 ' || true
