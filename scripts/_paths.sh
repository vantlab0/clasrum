curl -sS -m 10 http://127.0.0.1:8000/openapi.json > /tmp/openapi.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/openapi.json'))
for p in sorted(d['paths']):
    print(p)
PY
