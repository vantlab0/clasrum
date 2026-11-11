"""Profile endpoints: GET/PATCH /me (§5)."""
from __future__ import annotations


async def test_get_me(client, seeded_student, auth_headers):
    response = await client.get("/api/v1/me", headers=auth_headers(seeded_student))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == seeded_student["id"]
    assert body["email"] == seeded_student["email"]
    assert body["display_name"] == seeded_student["display_name"]
    assert body["default_level"] == "high_school"
    assert body["default_duration_minutes"] == 20


async def test_patch_me_updates_user_and_profile(client, seeded_student, auth_headers):
    from app.db import repositories
    from app.db.session import session_scope

    response = await client.patch(
        "/api/v1/me",
        headers=auth_headers(seeded_student),
        json={
            "display_name": "Renamed Student",
            "default_level": "university",
            "default_duration_minutes": 45,
            "preferences": {"chalk_color": "blue", "voice": "Cherry"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Renamed Student"
    assert body["default_level"] == "university"
    assert body["default_duration_minutes"] == 45
    assert body["preferences"] == {"chalk_color": "blue", "voice": "Cherry"}

    async with session_scope() as db:
        profile = await repositories.get_profile(db, seeded_student["id"])
        assert profile is not None
        assert profile.level == "university"
        assert profile.preferred_duration_minutes == 45

    # PATCH is a merge: a second call only touching one field must not wipe the
    # previously stored preferences.
    response = await client.patch(
        "/api/v1/me",
        headers=auth_headers(seeded_student),
        json={"preferences": {"voice": "Ethan"}},
    )
    assert response.status_code == 200
    assert response.json()["preferences"] == {
        "chalk_color": "blue",
        "voice": "Ethan",
    }
    assert response.json()["display_name"] == "Renamed Student"


async def test_patch_me_validation_errors(client, seeded_student, auth_headers):
    for payload in (
        {"display_name": "   "},
        {"default_duration_minutes": 0},
        {"default_duration_minutes": 10000},
        {"email": "new@example.com"},  # not a patchable field
    ):
        response = await client.patch(
            "/api/v1/me", headers=auth_headers(seeded_student), json=payload
        )
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"]["code"] == "invalid_arguments"


async def test_patch_me_requires_auth(client):
    response = await client.patch("/api/v1/me", json={"display_name": "x"})
    assert response.status_code == 401
