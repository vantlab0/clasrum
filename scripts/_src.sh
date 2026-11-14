sed -n '110,150p;265,300p' /opt/pwony/backend/.venv/lib/python3.12/site-packages/langgraph/checkpoint/sqlite/aio.py
echo "--- aiosqlite has is_alive?"
/opt/pwony/backend/.venv/bin/python - <<'PY'
import aiosqlite
print(aiosqlite.__version__ if hasattr(aiosqlite,'__version__') else 'unknown')
print([n for n in dir(aiosqlite.Connection) if 'alive' in n or 'open' in n])
PY
