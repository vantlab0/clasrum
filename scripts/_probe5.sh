sed -n '1,80p' /opt/avelut-realtime-proxy/server.mjs
echo "--- routes"
grep -nE 'pathname|upgrade|listen|/api|/ws' /opt/avelut-realtime-proxy/server.mjs | head -30
