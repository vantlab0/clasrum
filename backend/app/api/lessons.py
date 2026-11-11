"""Lesson lifecycle, board, transcript and notes REST endpoints (§5).

Every route below is lesson-scoped and goes through ``require_lesson_owner``
before touching any child table, so ownership is enforced in exactly one place.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Query

from app.contracts import dtos
from app.contracts.mappers import board_event_to_dto, note_to_detail, note_to_summary, transcript_entry_to_dto
from app.core import errors
from app.core.deps import CurrentStudent, DbSession, require_lesson_owner
from app.db import repositories
from app.lessons import service as lessons_service
from app.notes.service import request_note_generation, run_generation

router = APIRouter(prefix="/lessons", tags=["lessons"])


# ---------------------------------------------------------------------------
# CRUD / lifecycle
# ---------------------------------------------------------------------------


@router.post("", response_model=dtos.LessonDetail)
async def create_lesson(
    payload: dtos.CreateLessonRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.LessonDetail:
    lesson = await lessons_service.create_lesson(db, student, payload)
    return await lessons_service.build_lesson_detail(db, lesson)


@router.get("", response_model=dtos.LessonListResponse)
async def list_lessons(
    student: CurrentStudent,
    db: DbSession,
    status: str | None = Query(default=None, description="Filter by lesson status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dtos.LessonListResponse:
    return await lessons_service.list_lessons(
        db, student, status=status, limit=limit, offset=offset
    )


@router.get("/{lesson_id}", response_model=dtos.LessonDetail)
async def get_lesson(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.LessonDetail:
    lesson = await require_lesson_owner(lesson_id, student, db)
    return await lessons_service.build_lesson_detail(db, lesson)


@router.post("/{lesson_id}/resume", response_model=dtos.ResumeResponse)
async def resume_lesson(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.ResumeResponse:
    lesson = await require_lesson_owner(lesson_id, student, db)
    lesson, board, cursor, needs_prompt = await lessons_service.resume_lesson(db, lesson)
    detail = await lessons_service.build_lesson_detail(db, lesson)
    return dtos.ResumeResponse(
        lesson=detail,
        board=board,
        transcript_cursor=cursor,
        needs_notes_prompt=needs_prompt,
    )


@router.post("/{lesson_id}/end", response_model=dtos.LessonDetail)
async def end_lesson(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
    payload: dtos.EndLessonRequest | None = None,
) -> dtos.LessonDetail:
    lesson = await require_lesson_owner(lesson_id, student, db)
    reason = payload.reason if payload is not None else None
    lesson = await lessons_service.end_lesson(db, lesson, reason)
    return await lessons_service.build_lesson_detail(db, lesson)


# ---------------------------------------------------------------------------
# board (read-only REST backfill)
# ---------------------------------------------------------------------------


@router.get("/{lesson_id}/board", response_model=dtos.BoardSnapshotEnvelope)
async def get_board(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.BoardSnapshotEnvelope:
    lesson = await require_lesson_owner(lesson_id, student, db)
    return await lessons_service.get_board_snapshot(db, lesson, include_events=True)


@router.get("/{lesson_id}/board/events", response_model=dtos.BoardEventsResponse)
async def get_board_events(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
    after_sequence: int = Query(default=0, ge=0),
) -> dtos.BoardEventsResponse:
    lesson = await require_lesson_owner(lesson_id, student, db)
    events = await lessons_service.board_events(
        db, lesson.id, after_sequence=after_sequence
    )
    return dtos.BoardEventsResponse(events=events)


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------


@router.get("/{lesson_id}/transcript", response_model=dtos.TranscriptResponse)
async def get_transcript(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.TranscriptResponse:
    lesson = await require_lesson_owner(lesson_id, student, db)
    entries = await lessons_service.list_transcript(db, lesson.id)
    return dtos.TranscriptResponse(entries=entries)


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


@router.get("/{lesson_id}/notes", response_model=dtos.LessonNotesResponse)
async def list_lesson_notes(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.LessonNotesResponse:
    lesson = await require_lesson_owner(lesson_id, student, db)
    notes = await repositories.list_notes_for_lesson(db, lesson.id)
    return dtos.LessonNotesResponse(notes=[note_to_summary(note) for note in notes])


@router.post(
    "/{lesson_id}/notes/generate",
    response_model=dtos.GenerateNotesResponse,
)
async def generate_lesson_notes(
    lesson_id: str,
    payload: dtos.GenerateNotesRequest,
    background_tasks: BackgroundTasks,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.GenerateNotesResponse:
    """Consent-gated structured note generation.

    Returns the ``pending`` note immediately; the actual generation runs after
    the response is sent. ``consent`` must be exactly ``true`` (422 otherwise).
    """
    if payload.consent is not True:
        raise errors.invalid_arguments(
            "consent must be true to generate structured notes",
            details={"field": "consent"},
        )
    lesson = await require_lesson_owner(lesson_id, student, db)
    note = await request_note_generation(db, lesson)
    # Commit before yielding to the background task so it can see the row from
    # its own session (FastAPI runs background tasks before dependency teardown).
    await db.commit()
    detail = note_to_detail(note)
    background_tasks.add_task(run_generation, note.id)
    return dtos.GenerateNotesResponse(note=detail, status=detail.status)
