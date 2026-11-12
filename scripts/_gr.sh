cd /opt/ai-classroom/backend
grep -rn "is_alive" .venv/lib/python3.12/site-packages/langgraph/checkpoint/sqlite/ 2>/dev/null | head
echo "--- from_conn_string"
grep -n "is_alive\|def from_conn_string\|def setup" .venv/lib/python3.12/site-packages/langgraph/checkpoint/sqlite/aio.py | head -20
