"""Server-side websocket diagnostics (run on the VPS)."""
import asyncio
import json
import time
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
email = f"wsdiag.{int(time.time())}.{uuid.uuid4().hex[:6]}@example.com"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "classroom123", "display_name": "WS Diag"},
        )
        print("register", r.status_code)
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = await c.get("/api/v1/me", headers=headers)
        print("me", me.status_code, me.text[:200])
        r = await c.post(
            "/api/v1/lessons",
            headers=headers,
            json={
                "topic": "Diag",
                "subject": "Biology",
                "learning_level": "high_school",
                "duration_minutes": 10,
            },
        )
        print("lesson", r.status_code)
        lesson_id = r.json()["id"]

    url = f"ws://127.0.0.1:8000/api/v1/ws/lessons/{lesson_id}?token={token}"
    print("connecting app-direct:", url[:80] + "...")
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            print("OPEN", ws.response.status_code)
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(msg)
            print("frame type:", data.get("type"))
    except Exception as exc:
        print("app-direct error:", type(exc).__name__, exc)

    # Through the public domain (edge TLS -> nginx :10000 -> backend).
    url2 = f"wss://realtime.avelut.xyz/api/v1/ws/lessons/{lesson_id}?token={token}"
    print("connecting via public wss ...")
    try:
        async with websockets.connect(url2, open_timeout=20) as ws:
            print("OPEN", ws.response.status_code)
            msg = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(msg)
            print("frame type:", data.get("type"))
    except Exception as exc:
        print("public wss error:", type(exc).__name__, exc)


asyncio.run(main())
