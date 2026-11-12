set -e
hostname
echo "--- id"
id
echo "--- os"
lsb_release -ds || cat /etc/os-release | head -3
echo "--- arch"
uname -m
echo "--- docker"
docker --version 2>&1 || true
docker compose version 2>&1 || true
echo "--- web servers"
nginx -v 2>&1 || true
caddy version 2>&1 || true
echo "--- listening ports"
ss -tlnp 2>/dev/null | head -30
echo "--- disk"
df -h / | tail -1
echo "--- memory"
free -h | head -2
echo "--- dns check (from server)"
getent hosts realtime.avelut.xyz || true
