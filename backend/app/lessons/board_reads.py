"""Read-side board helpers shared by the REST routers.

Board *writes* live in ``app.board`` (backend-ai); this module only reads the
append-only event log and the latest snapshot directly from the lessons tables
so REST clients can restore the board without a WebSocket.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import dtos
from app.contracts.mappers import board_event_to_dto, snapshot_envelope
from app.db.models import Lesson, LessonBoardEvent
from app.db.repositories import (
    board_events_after,
    latest_board_snapshot,
    transcript_cursor,
)


async def get_board_events(
    db: AsyncSession,
    lesson_id: str,
    *,
    after_sequence: int = 0,
) -> list[dtos.BoardEvent]:
    rows: list[LessonBoardEvent] = await board_events_after(
        db, lesson_id, after_sequence=after_sequence
    )
    return [board_event_to_dto(row) for row in rows]


async def get_board_snapshot(
    db: AsyncSession,
    lesson: Lesson,
    *,
    include_events: bool = True,
) -> dtos.BoardSnapshotEnvelope:
    """Latest snapshot + the events that follow it (or the full replay log)."""
    snapshot = await latest_board_snapshot(db, lesson.id)
    after = int(snapshot.last_sequence) if snapshot is not None else 0
    events = await board_events_after(db, lesson.id, after_sequence=after) if include_events else []
    return snapshot_envelope(lesson, snapshot, events)


async def get_transcript_cursor(db: AsyncSession, lesson_id: str) -> int:
    """Sequence of the newest transcript entry (0 when empty)."""
    return await transcript_cursor(db, lesson_id)
