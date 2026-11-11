"""Auth: register / login / token handling / 401 envelope (§5, §7)."""
from __future__ import annotations

import re

VALID_PASSWORD = "classroom123"

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _register_body(**overrides):
    body = {
        "email": "new.student@example.com",
        "password": VALID_PASSWORD,
        "display_name": "New Student",
    }
    body.update(overrides)
    return body


async def test_register_returns_token_and_student(client):
    response = await client.post("/api/v1/auth/register", json=_register_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"access_token", "token_type", "student"}
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    student = body["student"]
    assert set(student) == {
        "id",
        "email",
        "display_name",
        "default_level",
        "default_duration_minutes",
        "preferences",
        "created_at",
    }
    assert student["email"] == "new.student@example.com"
    assert student["display_name"] == "New Student"
    assert student["preferences"] == {}
    assert TIMESTAMP_RE.match(student["created_at"]), student["created_at"]
    assert "password" not in str(student).lower()

    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["id"] == student["id"]


async def test_register_normalizes_email_and_rejects_duplicate(client):
    first = await client.post(
        "/api/v1/auth/register",
        json=_register_body(email="  Mixed.Case@Example.com "),
    )
    assert first.status_code == 200, first.text
    assert first.json()["student"]["email"] == "mixed.case@example.com"

    duplicate = await client.post(
        "/api/v1/auth/register",
        json=_register_body(email="mixed.case@example.com"),
    )
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["code"] == "conflict"
    assert error["retryable"] is False


async def test_register_rejects_invalid_email(client):
    response = await client.post(
        "/api/v1/auth/register", json=_register_body(email="not-an-email")
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_arguments"


async def test_register_rejects_weak_passwords(client):
    for weak in ("short7", "abcdefgh", "12345678", "a1"):
        response = await client.post(
            "/api/v1/auth/register",
            json=_register_body(email=f"weak.{weak}@example.com", password=weak),
        )
        assert response.status_code == 422, (weak, response.text)
        body = response.json()["error"]
        assert body["code"] == "invalid_arguments"
        assert "password" in body["message"].lower()


async def test_register_validation_envelope_for_missing_fields(client):
    response = await client.post("/api/v1/auth/register", json={"email": "x@y.com"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_arguments"
    assert error["details"]["errors"]
    locations = {tuple(item["loc"]) for item in error["details"]["errors"]}
    assert any("password" in loc for loc in locations)
    assert any("display_name" in loc for loc in locations)


async def test_login_success(client, seeded_student):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_student["email"], "password": seeded_student["password"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["student"]["id"] == seeded_student["id"]
    assert body["access_token"]


async def test_login_wrong_password_and_unknown_email(client, seeded_student):
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_student["email"], "password": "wrong-password1"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "unauthorized"

    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": VALID_PASSWORD},
    )
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "unauthorized"


async def test_me_requires_bearer_token(client):
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers.get("www-authenticate") == "Bearer"

    for bad_header in (
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
    ):
        response = await client.get("/api/v1/me", headers=bad_header)
        assert response.status_code == 401, bad_header
        assert response.json()["error"]["code"] == "unauthorized"


async def test_expired_and_tampered_tokens_are_rejected(client, seeded_student):
    from app.core.security import create_access_token

    expired = create_access_token(seeded_student["id"], expires_minutes=-5)
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401

    tampered = seeded_student["token"][:-2] + "xy"
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert response.status_code == 401


async def test_token_for_deleted_student_is_rejected(client, seeded_student):
    from app.core.security import create_access_token
    from app.db import repositories
    from app.db.session import session_scope

    async with session_scope() as db:
        await repositories.delete_student_data(db, seeded_student["id"])
    token = create_access_token(seeded_student["id"])
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
