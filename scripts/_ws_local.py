"""Local websocket reproduction against the dev server on :8010."""
import asyncio
import json
import time
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8010"


async def main() -> None:
    email = f"localdiag.{int(time.time())}.{uuid.uuid4().hex[:6]}@example.com"
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "classroom123", "display_name": "Diag"},
        )
        print("register", r.status_code, r.text[:100])
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        r = await c.post(
            "/api/v1/lessons",
            headers=h,
            json={"topic": "Diag", "subject": "Biology", "learning_level": "high_school", "duration_minutes": 10},
        )
        print("lesson", r.status_code, r.text[:100])
        lesson_id = r.json()["id"]

    url = f"ws://127.0.0.1:8010/api/v1/ws/lessons/{lesson_id}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            print("OPEN", ws.response.status_code)
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            print("frame:", json.loads(msg).get("type"))
    except Exception as exc:
        print("ws error:", type(exc).__name__, exc)


asyncio.run(main())
