echo "--- nginx sites"
ls -l /etc/nginx/sites-enabled/ /etc/nginx/sites-available/ 2>&1
echo "--- nginx config test"
sudo -n nginx -t 2>&1 || true
echo "--- port 80 owner"
sudo -n ss -tlnp | grep ':80 ' 2>&1 || true
echo "--- packages"
for p in python3 python3-venv python3-pip git curl certbot python3-certbot-nginx ufw; do
  printf '%s: ' "$p"
  dpkg -s "$p" >/dev/null 2>&1 && echo installed || echo missing
done
echo "--- python"
python3 --version
python3 -m venv --help >/dev/null 2>&1 && echo venv-ok || echo venv-missing
echo "--- ufw"
sudo -n ufw status 2>&1 || true
echo "--- external ip / ports"
curl -s -m 10 ifconfig.me; echo
echo "--- existing services"
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -30
echo "--- /opt"
ls -la /opt 2>&1
echo "--- home"
ls -la ~ | head -20
