echo "--- /etc/nginx/sites-available/realtime"
cat /etc/nginx/sites-available/realtime
echo "--- /opt/ai-classroom tree"
find /opt/ai-classroom -maxdepth 3 -not -path '*/.venv/*' | head -50
echo "--- service files"
ls -l /etc/systemd/system/ | grep -Ei 'classroom|realtime|avelut' || true
systemctl status ai-classroom --no-pager 2>&1 | head -20 || true
echo "--- avelut proxy tree"
find /opt/avelut-realtime-proxy -maxdepth 2 | head -40
echo "--- avelut service"
systemctl list-unit-files | grep -Ei 'avelut|realtime|classroom' || true
echo "--- existing local 8000/9000"
ss -tlnp | grep -E ':(8000|8001|9000|8080) ' || true
