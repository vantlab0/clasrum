/opt/ai-classroom/backend/.venv/bin/python - <<'PY'
import aiosqlite
c = aiosqlite.Connection
print("attrs:", [n for n in dir(c) if n.startswith('_') and 'run' in n or n in ('is_alive',)])
print("has is_alive:", hasattr(c, "is_alive"))
print("has __await__:", hasattr(c, "__await__"))
PY
echo "--- latest checkpoint-sqlite versions"
curl -s -m 20 https://pypi.org/pypi/langgraph-checkpoint-sqlite/json | /opt/ai-classroom/backend/.venv/bin/python - <<'PY'
import json, sys
d = json.load(sys.stdin)
vs = list(d["releases"].keys())
print(vs[-12:])
print("latest:", d["info"]["version"])
PY
