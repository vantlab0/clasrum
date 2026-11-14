cd /opt/pwony/backend
~/.local/bin/uv pip list --python .venv/bin/python 2>/dev/null | grep -Ei 'langgraph|aiosqlite|langchain|sqlalchemy|fastapi|uvicorn|starlette|pydantic'
