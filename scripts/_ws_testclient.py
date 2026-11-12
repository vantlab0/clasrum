"""Reproduce the lesson websocket handshake locally with Starlette's TestClient."""
import os
import sys

os.environ.update(
    {
        "DATABASE_URL": "sqlite+aiosqlite:///./wsdebug.db",
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "CORS_ORIGINS": '["*"]',
        "QWEN_FAKE_MODE": "true",
        "JWT_SECRET": "local-debug-secret-0123456789abcdef0123456789abcdef",
    }
)

sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/auth/register",
            json={
                "email": "ws.debug@example.com",
                "password": "classroom123",
                "display_name": "WS Debug",
            },
        )
        print("register", r.status_code)
        if r.status_code == 409:
            r = client.post(
                "/api/v1/auth/login",
                json={"email": "ws.debug@example.com", "password": "classroom123"},
            )
            print("login", r.status_code)
        token = r.json()["access_token"]
        r = client.post(
            "/api/v1/lessons",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "topic": "Debug",
                "subject": "Biology",
                "learning_level": "high_school",
                "duration_minutes": 10,
            },
        )
        print("lesson", r.status_code)
        lesson_id = r.json()["id"]

        url = f"/api/v1/ws/lessons/{lesson_id}?token={token}"
        print("connecting", url[:70])
        import logging

        logging.basicConfig(level=logging.INFO)
        try:
            with client.websocket_connect(url) as ws:
                print("connected; first frame:", ws.receive_json())
                ws.close()
        except Exception as exc:  # noqa: BLE001
            print("ws failed:", type(exc).__name__, "code=", getattr(exc, "code", None), exc)


if __name__ == "__main__":
    main()
