echo "--- python versions"
ls /usr/bin/python3.* 2>/dev/null
which python3.11 python3.12 2>/dev/null || true
echo "--- apt policy"
apt-cache policy python3.11 2>/dev/null | head -8
echo "--- internet"
curl -s -m 15 -o /dev/null -w 'pypi:%{http_code}\n' https://pypi.org/simple/
curl -s -m 15 -o /dev/null -w 'deadsnakes:%{http_code}\n' https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/
echo "--- certbot state"
sudo -n ls /etc/letsencrypt/live 2>&1 || true
sudo -n certbot certificates 2>&1 | head -20 || true
echo "--- nginx has ssl?"
grep -R "listen 443" /etc/nginx/sites-enabled/ 2>/dev/null || echo none
