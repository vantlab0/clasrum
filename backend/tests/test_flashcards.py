"""Flashcards: generation, list, detail, and lesson ownership (§5)."""
from __future__ import annotations

import asyncio
import time


async def _wait_for_set(
    client,
    headers: dict,
    lesson_id: str,
    set_id: str,
    *,
    timeout: float = 5.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        response = await client.get(
            f"/api/v1/lessons/{lesson_id}/flashcards/{set_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] != "pending":
            return last
        await asyncio.sleep(0.02)
    assert last is not None
    return last


async def test_generate_list_and_fetch_flashcards(
    client, seeded_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student, topic="Cell biology")
    headers = auth_headers(seeded_student)

    generated = await client.post(
        f"/api/v1/lessons/{lesson['id']}/flashcards/generate",
        headers=headers,
        json={"topic": "Cell biology", "subject": "Biology", "class_level": "high_school"},
    )
    assert generated.status_code == 200, generated.text
    summary = generated.json()
    assert summary["id"]
    assert summary["topic"] == "Cell biology"
    assert summary["status"] in {"pending", "ready"}

    detail = await _wait_for_set(
        client, headers, lesson["id"], summary["id"]
    )
    assert detail["status"] == "ready", detail
    assert detail["error"] is None
    assert len(detail["flashcards"]) >= 1
    card = detail["flashcards"][0]
    assert card["question"].strip()
    assert card["answer"].strip()
    assert card["topic"] == "Cell biology"

    listing = await client.get(
        f"/api/v1/lessons/{lesson['id']}/flashcards", headers=headers
    )
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == summary["id"]
    assert payload["items"][0]["flashcard_count"] == len(detail["flashcards"])


async def test_flashcards_are_lesson_owner_scoped(
    client,
    seeded_student,
    other_student,
    auth_headers,
    make_lesson,
):
    lesson = await make_lesson(seeded_student, topic="Photosynthesis")
    owner_headers = auth_headers(seeded_student)
    generated = await client.post(
        f"/api/v1/lessons/{lesson['id']}/flashcards/generate",
        headers=owner_headers,
        json={"topic": "Photosynthesis"},
    )
    assert generated.status_code == 200, generated.text
    set_id = generated.json()["id"]

    other_headers = auth_headers(other_student)
    detail = await client.get(
        f"/api/v1/lessons/{lesson['id']}/flashcards/{set_id}",
        headers=other_headers,
    )
    # Cross-student access is forbidden (403), per the API ownership convention.
    assert detail.status_code == 403, detail.text

    listing = await client.get(
        f"/api/v1/lessons/{lesson['id']}/flashcards", headers=other_headers
    )
    assert listing.status_code == 403, listing.text

    generate = await client.post(
        f"/api/v1/lessons/{lesson['id']}/flashcards/generate",
        headers=other_headers,
        json={"topic": "Photosynthesis"},
    )
    assert generate.status_code == 403, generate.text


async def test_missing_flashcard_set_returns_not_found(
    client, seeded_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student)
    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/flashcards/does-not-exist",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
