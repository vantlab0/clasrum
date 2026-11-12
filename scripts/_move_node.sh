set -e
echo "--- current .env"
sudo -n sed -n '1,5p' /opt/avelut-realtime-proxy/.env | sed 's/ALIBABA_API_KEY=.*/ALIBABA_API_KEY=<redacted>/'
echo "--- moving Node proxy to port 10001"
sudo -n sed -i 's/^PORT=.*/PORT=10001/' /opt/avelut-realtime-proxy/.env
sudo -n env PATH=$PATH:/usr/bin pm2 restart avelut-proxy --update-env
sleep 3
echo "--- listeners"
sudo -n ss -tlnp | grep -E ':(10000|10001) ' || true
echo "--- health on 10001"
curl -sS -m 10 http://127.0.0.1:10001/health
echo
