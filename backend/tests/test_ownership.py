"""Ownership enforcement: 403 cross-student, 404 missing, 401 unauthenticated."""
from __future__ import annotations

import pytest


async def test_lesson_scoped_endpoints_are_forbidden_for_other_student(
    client, seeded_student, other_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student)
    lesson_id = lesson["id"]
    headers = auth_headers(other_student)

    calls = [
        client.get(f"/api/v1/lessons/{lesson_id}", headers=headers),
        client.post(f"/api/v1/lessons/{lesson_id}/resume", headers=headers),
        client.post(f"/api/v1/lessons/{lesson_id}/end", headers=headers, json={}),
        client.get(f"/api/v1/lessons/{lesson_id}/board", headers=headers),
        client.get(f"/api/v1/lessons/{lesson_id}/board/events", headers=headers),
        client.get(f"/api/v1/lessons/{lesson_id}/transcript", headers=headers),
        client.get(f"/api/v1/lessons/{lesson_id}/notes", headers=headers),
        client.post(
            f"/api/v1/lessons/{lesson_id}/notes/generate",
            headers=headers,
            json={"consent": True},
        ),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "forbidden"


async def test_lesson_scoped_endpoints_404_for_unknown_lesson(
    client, seeded_student, auth_headers
):
    headers = auth_headers(seeded_student)
    calls = [
        client.get("/api/v1/lessons/missing-lesson", headers=headers),
        client.post("/api/v1/lessons/missing-lesson/resume", headers=headers),
        client.post("/api/v1/lessons/missing-lesson/end", headers=headers, json={}),
        client.get("/api/v1/lessons/missing-lesson/board", headers=headers),
        client.get("/api/v1/lessons/missing-lesson/board/events", headers=headers),
        client.get("/api/v1/lessons/missing-lesson/transcript", headers=headers),
        client.get("/api/v1/lessons/missing-lesson/notes", headers=headers),
        client.post(
            "/api/v1/lessons/missing-lesson/notes/generate",
            headers=headers,
            json={"consent": True},
        ),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"


async def test_lesson_scoped_endpoints_require_auth(client):
    calls = [
        client.get("/api/v1/lessons"),
        client.post("/api/v1/lessons", json={"topic": "abc", "duration_minutes": 20, "learning_level": "x"}),
        client.get("/api/v1/lessons/some-id"),
        client.post("/api/v1/lessons/some-id/resume"),
        client.post("/api/v1/lessons/some-id/end", json={}),
        client.get("/api/v1/lessons/some-id/board"),
        client.get("/api/v1/lessons/some-id/transcript"),
        client.get("/api/v1/lessons/some-id/notes"),
        client.post(
            "/api/v1/lessons/some-id/notes/generate", json={"consent": True}
        ),
        client.get("/api/v1/notes"),
        client.get("/api/v1/notes/some-note"),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "unauthorized"


async def test_lesson_lists_are_isolated_between_students(
    client, seeded_student, other_student, auth_headers, make_lesson
):
    await make_lesson(seeded_student)
    seeded_list = await client.get(
        "/api/v1/lessons", headers=auth_headers(seeded_student)
    )
    other_list = await client.get(
        "/api/v1/lessons", headers=auth_headers(other_student)
    )
    assert seeded_list.json()["total"] == 1
    assert other_list.json()["total"] == 0
    assert other_list.json()["items"] == []


async def test_note_detail_is_isolated_between_students(
    client,
    seeded_student,
    other_student,
    auth_headers,
    make_lesson,
    append_turns,
    seed_board,
    wait_for_note,
):
    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["This is a lesson about cells."])
    await seed_board(
        lesson["id"],
        [
            {
                "type": "write_heading",
                "element_id": "el_h",
                "payload": {"text": "Cells", "position": {"x": 0.1, "y": 0.1}},
            }
        ],
    )
    await client.post(
        f"/api/v1/lessons/{lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={},
    )
    generated = await client.post(
        f"/api/v1/lessons/{lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": True},
    )
    assert generated.status_code == 200, generated.text
    note_id = generated.json()["note"]["id"]
    await wait_for_note(seeded_student, note_id)

    foreign = await client.get(
        f"/api/v1/notes/{note_id}", headers=auth_headers(other_student)
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "forbidden"

    other_notes = await client.get(
        "/api/v1/notes", headers=auth_headers(other_student)
    )
    assert other_notes.status_code == 200
    assert other_notes.json()["total"] == 0


@pytest.mark.parametrize("bad_token", ["garbage", "a.b.c", ""])
async def test_garbage_tokens_are_unauthorized(client, bad_token):
    response = await client.get(
        "/api/v1/lessons", headers={"Authorization": f"Bearer {bad_token}"}
    )
    assert response.status_code == 401
