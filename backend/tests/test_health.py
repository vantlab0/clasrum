"""Health endpoint (§5)."""
from __future__ import annotations


async def test_healthz_reports_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["qwen_configured"] is False  # no API key in the test env
    assert isinstance(body["version"], str) and body["version"]


async def test_healthz_needs_no_auth(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_openapi_schema_lists_contract_paths(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/me",
        "/api/v1/me/data",
        "/api/v1/lessons",
        "/api/v1/lessons/{lesson_id}",
        "/api/v1/lessons/{lesson_id}/resume",
        "/api/v1/lessons/{lesson_id}/end",
        "/api/v1/lessons/{lesson_id}/board",
        "/api/v1/lessons/{lesson_id}/board/events",
        "/api/v1/lessons/{lesson_id}/transcript",
        "/api/v1/lessons/{lesson_id}/notes",
        "/api/v1/lessons/{lesson_id}/notes/generate",
        "/api/v1/notes",
        "/api/v1/notes/{note_id}",
        "/healthz",
    }
    missing = expected - set(paths)
    assert not missing, f"missing contract paths: {sorted(missing)}"
