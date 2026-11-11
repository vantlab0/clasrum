"""Transcript persistence: ordering, cursors, counter healing (§8)."""
from __future__ import annotations

from sqlalchemy import select, update

from app.db.models import LessonTranscript
from app.db.repositories import fetch_transcript_entries, get_transcript, transcript_cursor


async def test_transcript_empty_for_new_lesson(
    client, seeded_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student)
    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/transcript",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200
    assert response.json() == {"entries": []}


async def test_transcript_entries_are_ordered_and_typed(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    lesson = await make_lesson(seeded_student)
    await append_turns(
        lesson["id"],
        [
            "Welcome to the lesson.",
            ("student", "Thank you."),
            ("tutor", "Let us begin with the basics."),
            ("system", "Recording started."),
        ],
    )
    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/transcript",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [entry["sequence"] for entry in entries] == [1, 2, 3, 4]
    assert [entry["speaker"] for entry in entries] == [
        "tutor",
        "student",
        "tutor",
        "system",
    ]
    assert entries[0]["text"] == "Welcome to the lesson."
    assert entries[1]["text"] == "Thank you."
    for entry in entries:
        assert set(entry) == {
            "id",
            "session_id",
            "sequence",
            "speaker",
            "text",
            "started_at",
            "ended_at",
        }
        assert entry["session_id"] == lesson["id"]
        assert entry["started_at"].endswith("Z")
        assert entry["ended_at"] is None


async def test_transcript_counters_and_cursor(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["one", "two", "three"])

    async with session_scope() as db:
        transcript = await get_transcript(db, lesson["id"])
        assert transcript is not None
        assert transcript.entry_count == 3
        assert transcript.last_sequence == 3
        assert await transcript_cursor(db, lesson["id"]) == 3
        after = await fetch_transcript_entries(db, lesson["id"], after_sequence=1)
        assert [entry.sequence for entry in after] == [2, 3]


async def test_transcript_append_heals_drifted_counter(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    """A stale ``last_sequence`` must not produce duplicate sequences."""
    from app.lessons.transcripts import append_transcript_entry
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["one", "two", "three"])

    async with session_scope() as db:
        await db.execute(
            update(LessonTranscript)
            .where(LessonTranscript.lesson_id == lesson["id"])
            .values(last_sequence=1)
        )
    async with session_scope() as db:
        entry = await append_transcript_entry(db, lesson["id"], "tutor", "four")
        assert entry.sequence == 4
    async with session_scope() as db:
        transcript = await get_transcript(db, lesson["id"])
        assert transcript is not None
        assert transcript.last_sequence == 4
        assert transcript.entry_count == 4
        rows = (
            await db.execute(
                select(LessonTranscript.id).where(
                    LessonTranscript.lesson_id == lesson["id"]
                )
            )
        ).scalars().all()
        assert len(rows) == 1


async def test_transcript_rejects_invalid_turns(
    client, seeded_student, auth_headers, make_lesson
):
    import pytest

    from app.lessons.transcripts import TranscriptError, append_transcript_entry
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student)
    async with session_scope() as db:
        with pytest.raises(TranscriptError):
            await append_transcript_entry(db, lesson["id"], "narrator", "hello")
        with pytest.raises(TranscriptError):
            await append_transcript_entry(db, lesson["id"], "tutor", "   ")
