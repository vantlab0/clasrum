echo "--- uv"
ls -l ~/.local/bin/uv 2>/dev/null && ~/.local/bin/uv --version || echo no-uv
echo "--- apt lock"
sudo -n fuser /var/lib/dpkg/lock-frontend 2>/dev/null && echo apt-busy || echo apt-free
echo "--- edge headers (server side view via host header)"
curl -s -D - -o /dev/null http://127.0.0.1/api/v1/health -H 'Host: realtime.avelut.xyz' || true
echo "--- memory/swap"
free -m | head -3
echo "--- sqlite lib"
python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
