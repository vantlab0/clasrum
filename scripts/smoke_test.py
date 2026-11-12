"""End-to-end smoke test against the deployed public endpoint."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx

BASE = os.environ.get("SMOKE_BASE", "https://realtime.avelut.xyz")
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")


def main() -> int:
    email = f"smoke.{int(time.time())}.{uuid.uuid4().hex[:6]}@example.com"
    password = "classroom123"
    with httpx.Client(base_url=BASE, timeout=60.0) as c:
        r = c.get("/healthz")
        print("health:", r.status_code, r.text[:120])
        r = c.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "display_name": "Smoke Test"},
        )
        print("register:", r.status_code)
        if r.status_code != 200:
            print(r.text[:500])
            return 1
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        r = c.get("/api/v1/me", headers=headers)
        print("me:", r.status_code, r.json().get("email"))

        r = c.post(
            "/api/v1/lessons",
            headers=headers,
            json={
                "topic": "Introduction to Photosynthesis",
                "subject": "Biology",
                "learning_level": "high_school",
                "duration_minutes": 20,
            },
        )
        print("create lesson:", r.status_code)
        if r.status_code != 200:
            print(r.text[:800])
            return 1
        lesson = r.json()
        lesson_id = lesson["id"]
        print("  lesson:", lesson_id, "concepts:", len(lesson["plan"]["concepts"]))

        r = c.get("/api/v1/lessons", headers=headers)
        print("list lessons:", r.status_code, "count:", len(r.json().get("items", r.json())))

        ok = asyncio.run(ws_check(lesson_id, token))
        print("websocket:", "ok" if ok else "FAILED")

        r = c.post(f"/api/v1/lessons/{lesson_id}/end", headers=headers, json={"reason": "smoke"})
        print("end lesson:", r.status_code)
    return 0 if ok else 1


async def ws_check(lesson_id: str, token: str) -> bool:
    import websockets

    url = f"{WS_BASE}/api/v1/ws/lessons/{lesson_id}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=20, close_timeout=5) as ws:
            print("  ws open:", ws.response.status_code if ws.response else "?")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                data = json.loads(message) if isinstance(message, str) else message
                kind = data.get("type") if isinstance(data, dict) else type(data).__name__
                print("  frame:", kind)
                if kind == "session.ready":
                    return True
                if kind == "error":
                    print("  error payload:", json.dumps(data)[:400])
                    return False
            print("  no session.ready within 25s")
            return False
    except Exception as exc:  # noqa: BLE001
        print("  ws error:", type(exc).__name__, exc)
        return False


if __name__ == "__main__":
    raise SystemExit(main())
