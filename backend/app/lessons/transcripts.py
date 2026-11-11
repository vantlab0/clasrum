"""Transcript persistence: per-lesson sequence allocation and reads.

Every lesson has at most one ``lesson_transcripts`` counter row. Appending is
serialised by an atomic ``UPDATE ... RETURNING last_sequence + 1`` inside a
savepoint, so concurrent writers (realtime tutor + client typed fallback) get
strictly increasing, gap-free per-lesson sequences. If the counter row somehow
drifts, the unique constraint ``uq_transcript_lesson_seq`` raises and the
append retries with a fresh allocation.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.constants import Speaker
from app.db.models import LessonTranscript, LessonTranscriptEntry
from app.db.repositories import fetch_transcript_entries, transcript_cursor

VALID_SPEAKERS = frozenset({Speaker.TUTOR, Speaker.STUDENT, Speaker.SYSTEM})
MAX_SEQUENCE_RETRIES = 5
MAX_ENTRY_CHARS = 20_000


class TranscriptError(ValueError):
    """Invalid transcript append request (unknown speaker / empty text)."""


async def get_or_create_transcript(
    db: AsyncSession, lesson_id: str
) -> LessonTranscript:
    result = await db.execute(
        select(LessonTranscript).where(LessonTranscript.lesson_id == lesson_id)
    )
    transcript = result.scalar_one_or_none()
    if transcript is not None:
        return transcript
    try:
        async with db.begin_nested():
            transcript = LessonTranscript(
                lesson_id=lesson_id, entry_count=0, last_sequence=0
            )
            db.add(transcript)
            await db.flush()
        return transcript
    except IntegrityError:
        # Another writer created it concurrently; re-read the winner.
        result = await db.execute(
            select(LessonTranscript).where(LessonTranscript.lesson_id == lesson_id)
        )
        existing = result.scalar_one_or_none()
        if existing is None:  # pragma: no cover - defensive
            raise
        return existing


async def append_transcript_entry(
    db: AsyncSession,
    lesson_id: str,
    speaker: str,
    text: str,
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    transcript: LessonTranscript | None = None,
) -> LessonTranscriptEntry:
    """Persist one transcript turn with an atomically allocated sequence.

    The caller owns the surrounding transaction (``get_db`` commits it).
    ``speaker`` must be one of ``tutor``/``student``/``system`` and ``text``
    must be non-empty; realtime adapters should treat ``TranscriptError`` as a
    validation failure and drop the turn rather than kill the socket.
    """
    if speaker not in VALID_SPEAKERS:
        raise TranscriptError(f"speaker must be one of {sorted(VALID_SPEAKERS)}")
    if not isinstance(text, str):
        raise TranscriptError("text must be a string")
    text = text.strip()
    if not text:
        raise TranscriptError("text must not be empty")
    if len(text) > MAX_ENTRY_CHARS:
        raise TranscriptError(f"text must be at most {MAX_ENTRY_CHARS} characters")

    if transcript is None:
        transcript = await get_or_create_transcript(db, lesson_id)
    stamp = started_at or utcnow()

    last_error: IntegrityError | None = None
    for _attempt in range(MAX_SEQUENCE_RETRIES):
        entry = LessonTranscriptEntry(
            lesson_id=lesson_id,
            transcript_id=transcript.id,
            sequence=0,
            speaker=speaker,
            text=text,
            started_at=stamp,
            ended_at=ended_at,
        )
        try:
            async with db.begin_nested():
                # Sequence = one past the newest persisted turn (and never less
                # than the counter), so a drifted counter heals instead of
                # looping on the same colliding value.
                max_sequence = (
                    await db.execute(
                        select(
                            func.coalesce(func.max(LessonTranscriptEntry.sequence), 0)
                        ).where(LessonTranscriptEntry.lesson_id == lesson_id)
                    )
                ).scalar_one()
                sequence = max(int(max_sequence), int(transcript.last_sequence or 0)) + 1
                await db.execute(
                    update(LessonTranscript)
                    .where(LessonTranscript.id == transcript.id)
                    .values(
                        last_sequence=sequence,
                        entry_count=LessonTranscript.entry_count + 1,
                        updated_at=utcnow(),
                    )
                )
                entry.sequence = int(sequence)
                db.add(entry)
                await db.flush()
            await db.refresh(transcript)
            return entry
        except IntegrityError as exc:  # unique (lesson_id, sequence) collision
            last_error = exc
            # Savepoint rolled back both the counter bump and the insert; drop
            # the failed pending instance before retrying with a new sequence.
            if entry in db:
                db.expunge(entry)
            await db.refresh(transcript)
            continue
    assert last_error is not None
    raise last_error


async def append_many(
    db: AsyncSession,
    lesson_id: str,
    turns: list[dict],
) -> list[LessonTranscriptEntry]:
    """Append a batch of ``{speaker, text, started_at?, ended_at?}`` turns."""
    transcript = await get_or_create_transcript(db, lesson_id)
    entries: list[LessonTranscriptEntry] = []
    for turn in turns:
        entries.append(
            await append_transcript_entry(
                db,
                lesson_id,
                turn["speaker"],
                turn["text"],
                started_at=turn.get("started_at"),
                ended_at=turn.get("ended_at"),
                transcript=transcript,
            )
        )
    return entries


async def fetch_entries(
    db: AsyncSession,
    lesson_id: str,
    *,
    after_sequence: int = 0,
) -> list[LessonTranscriptEntry]:
    return await fetch_transcript_entries(db, lesson_id, after_sequence=after_sequence)


__all__ = [
    "TranscriptError",
    "append_many",
    "append_transcript_entry",
    "fetch_entries",
    "get_or_create_transcript",
    "transcript_cursor",
]
