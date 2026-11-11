"""Structured notes: consent gate, generation success/failure, list/detail (§5)."""
from __future__ import annotations

NOTE_EVENTS = [
    {
        "type": "write_heading",
        "element_id": "el_heading",
        "payload": {"text": "Cell Structure", "position": {"x": 0.05, "y": 0.05}},
    },
    {
        "type": "write_text",
        "element_id": "el_text",
        "payload": {
            "text": "Cells are the basic unit of life.",
            "style": "body",
            "position": {"x": 0.05, "y": 0.15},
        },
    },
    {
        "type": "write_definition",
        "element_id": "el_definition",
        "payload": {
            "term": "Cell",
            "text": "The smallest structural and functional unit of an organism.",
            "position": {"x": 0.05, "y": 0.25},
        },
    },
    {
        "type": "write_equation",
        "element_id": "el_equation",
        "payload": {"latex": "E = mc^2", "position": {"x": 0.05, "y": 0.4}},
    },
]

TRANSCRIPT_TURNS = [
    "Today we are going to look at cell structure.",
    ("student", "What is a cell?"),
    "A cell is the smallest structural and functional unit of an organism.",
    ("student", "So the nucleus controls the cell?"),
    "Yes, the nucleus stores genetic material and controls cell activity.",
]


async def _ended_lesson_with_content(
    client, student, headers, make_lesson, append_turns, seed_board
) -> dict:
    lesson = await make_lesson(student)
    await append_turns(lesson["id"], TRANSCRIPT_TURNS)
    await seed_board(lesson["id"], NOTE_EVENTS)
    end = await client.post(
        f"/api/v1/lessons/{lesson['id']}/end", headers=headers, json={}
    )
    assert end.status_code == 200, end.text
    return lesson


async def test_generate_notes_requires_consent(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["Some teaching content."])
    for payload in ({}, {"consent": False}):
        response = await client.post(
            f"/api/v1/lessons/{lesson['id']}/notes/generate",
            headers=auth_headers(seeded_student),
            json=payload,
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "invalid_arguments"
        assert "consent" in error["message"].lower()

    bad_type = await client.post(
        f"/api/v1/lessons/{lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": "yes"},
    )
    assert bad_type.status_code == 422

    listing = await client.get(
        f"/api/v1/lessons/{lesson['id']}/notes",
        headers=auth_headers(seeded_student),
    )
    assert listing.status_code == 200
    assert listing.json()["notes"] == []


async def test_generate_notes_requires_transcript(
    client, seeded_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student)
    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": True},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"]["reason"] == "transcript_missing"


async def test_offline_note_generation_success(
    client,
    seeded_student,
    auth_headers,
    make_lesson,
    append_turns,
    seed_board,
    wait_for_note,
):
    from app.db import repositories
    from app.db.session import session_scope

    lesson = await _ended_lesson_with_content(
        client, seeded_student, auth_headers(seeded_student), make_lesson, append_turns, seed_board
    )
    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["note"]["status"] == "pending"
    assert body["note"]["lesson_id"] == lesson["id"]
    note_id = body["note"]["id"]

    note = await wait_for_note(seeded_student, note_id)
    assert note["status"] == "ready"
    assert note["generated_by"] == "offline:deterministic"
    assert note["error"] is None
    assert note["title"].startswith("Introduction to Photosynthesis")
    assert note["content_markdown"].startswith("# ")
    assert "## " in note["content_markdown"]
    assert note["source_transcript_id"] is not None
    assert note["word_count"] > 30

    headings = [section["heading"] for section in note["sections"]]
    assert "Overview" in headings
    assert "Key Takeaways" in headings
    assert "Revision Questions" in headings
    assert "Cell Structure" in headings
    assert any(section.get("latex") == "E = mc^2" for section in note["sections"])

    # Board-derived content is cited with the real element ids.
    async with session_scope() as db:
        row = await repositories.get_note(db, note_id)
        assert row is not None
        references = (row.board_reference or {}).get("elements") or []
        ids = {reference["element_id"] for reference in references}
        assert {"el_heading", "el_text", "el_definition", "el_equation"} <= ids
        assert (row.board_reference or {}).get("key_takeaways")
        assert (row.board_reference or {}).get("revision_questions")

    lesson_notes = await client.get(
        f"/api/v1/lessons/{lesson['id']}/notes",
        headers=auth_headers(seeded_student),
    )
    assert lesson_notes.status_code == 200
    summaries = lesson_notes.json()["notes"]
    assert len(summaries) == 1
    assert summaries[0]["id"] == note_id
    assert summaries[0]["status"] == "ready"
    assert summaries[0]["word_count"] == note["word_count"]

    all_notes = await client.get("/api/v1/notes", headers=auth_headers(seeded_student))
    assert all_notes.status_code == 200
    assert all_notes.json()["total"] == 1
    assert all_notes.json()["items"][0]["id"] == note_id

    # The transcript is untouched by note generation.
    transcript = await client.get(
        f"/api/v1/lessons/{lesson['id']}/transcript",
        headers=auth_headers(seeded_student),
    )
    assert transcript.status_code == 200
    assert len(transcript.json()["entries"]) == len(TRANSCRIPT_TURNS)

    # Once notes exist the resume prompt disappears.
    resume = await client.post(
        f"/api/v1/lessons/{lesson['id']}/resume",
        headers=auth_headers(seeded_student),
    )
    assert resume.status_code == 200
    assert resume.json()["needs_notes_prompt"] is False


async def test_note_generation_failure_is_persisted(
    client,
    seeded_student,
    auth_headers,
    make_lesson,
    append_turns,
    seed_board,
    wait_for_note,
    monkeypatch,
):
    import app.notes.service as notes_service

    lesson = await _ended_lesson_with_content(
        client, seeded_student, auth_headers(seeded_student), make_lesson, append_turns, seed_board
    )

    async def _explode(context, settings):  # noqa: ANN001, ANN202
        raise RuntimeError("model exploded")

    monkeypatch.setattr(notes_service, "generate", _explode)

    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": True},
    )
    assert response.status_code == 200, response.text
    note_id = response.json()["note"]["id"]

    note = await wait_for_note(seeded_student, note_id)
    assert note["status"] == "failed"
    assert "model exploded" in note["error"]
    assert note["content_markdown"] is None
    assert note["sections"] == []

    transcript = await client.get(
        f"/api/v1/lessons/{lesson['id']}/transcript",
        headers=auth_headers(seeded_student),
    )
    assert transcript.status_code == 200
    assert len(transcript.json()["entries"]) == len(TRANSCRIPT_TURNS)

    lesson_notes = await client.get(
        f"/api/v1/lessons/{lesson['id']}/notes",
        headers=auth_headers(seeded_student),
    )
    assert lesson_notes.json()["notes"][0]["status"] == "failed"


async def test_note_detail_unknown_and_cross_student(
    client, seeded_student, other_student, auth_headers
):
    missing = await client.get(
        "/api/v1/notes/not-a-note", headers=auth_headers(seeded_student)
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
