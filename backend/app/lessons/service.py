"""Lesson lifecycle service: create, list, detail, resume, end.

Teaching truth lives in the LangGraph checkpoint (thread id == lesson id);
``lessons`` rows are the durable index the REST layer reads. The service is the
only place that mutates lesson lifecycle columns, and it delegates all teaching
state transitions to ``app.graph.service``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import dtos
from app.contracts.mappers import ensure_utc, lesson_detail, lesson_summary
from app.core import errors
from app.db.base import utcnow
from app.db.constants import LessonStatus
from app.db.models import Lesson, User
from app.db.repositories import (
    board_events_after,
    create_lesson as create_lesson_row,
    get_lesson_plan,
    latest_board_snapshot,
    lesson_has_notes,
    lesson_has_transcript,
    list_assessments,
    list_lessons as list_lesson_rows,
    notes_presence_map,
    transcript_presence_map,
    update_lesson as update_lesson_row,
)
from app.graph import service as graph_service
from app.lessons.board_reads import get_board_snapshot, get_transcript_cursor
from app.lessons.transcripts import fetch_entries

logger = logging.getLogger(__name__)

ENDED_STATUSES = frozenset({LessonStatus.COMPLETED, LessonStatus.ABANDONED})
RESUMABLE_STATUSES = frozenset(
    {LessonStatus.CREATED, LessonStatus.LIVE, LessonStatus.PAUSED}
)


async def _graph_teaching_state(lesson_id: str) -> dict | None:
    """Read teaching state; graph outages must not break REST reads."""
    try:
        state = await graph_service.get_teaching_state(lesson_id)
    except Exception:  # pragma: no cover - defensive against graph/storage faults
        logger.warning("teaching state unavailable for lesson %s", lesson_id, exc_info=True)
        return None
    return state if isinstance(state, dict) else None


async def build_lesson_detail(
    db: AsyncSession,
    lesson: Lesson,
    *,
    now: datetime | None = None,
    teaching_state: dict | None = None,
    assessment_summary: str | None = None,
) -> dtos.LessonDetail:
    """Assemble the complete ``LessonDetail`` DTO for one lesson."""
    plan = await get_lesson_plan(db, lesson.id)
    state = teaching_state if teaching_state is not None else await _graph_teaching_state(lesson.id)
    has_transcript = await lesson_has_transcript(db, lesson.id)
    has_notes = await lesson_has_notes(db, lesson.id)
    if assessment_summary is None:
        assessment_summary = await _assessment_summary(db, lesson, state)
    return lesson_detail(
        lesson,
        plan=plan,
        teaching_state=state,
        has_transcript=has_transcript,
        has_notes=has_notes,
        assessment_summary=assessment_summary,
        now=now,
    )


async def _assessment_summary(
    db: AsyncSession, lesson: Lesson, state: dict | None
) -> str | None:
    meta = lesson.session_metadata or {}
    stored = meta.get("assessment_summary")
    if stored:
        return str(stored)
    if state and state.get("assessment_summary"):
        return str(state["assessment_summary"])
    rows = await list_assessments(db, lesson.id)
    if not rows:
        return None
    answered = [row for row in rows if row.correct is not None]
    if not answered:
        return f"{len(rows)} check question{'s' if len(rows) != 1 else ''} asked"
    correct = sum(1 for row in answered if row.correct)
    return (
        f"{correct}/{len(answered)} check questions correct"
        f" ({len(rows)} asked)"
    )


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def create_lesson(
    db: AsyncSession, student: User, request: dtos.CreateLessonRequest
) -> Lesson:
    """Create the DB lesson and initialise its LangGraph state + plan."""
    lesson = await create_lesson_row(
        db,
        student_id=student.id,
        topic=request.topic,
        subject=request.subject,
        learning_level=request.learning_level,
        duration_minutes=request.duration_minutes,
        status=LessonStatus.CREATED,
    )
    now = utcnow()
    lesson.started_at = now
    lesson.last_activity_at = now
    await db.flush()

    try:
        result = await graph_service.start_lesson(db, lesson)
    except Exception as exc:
        logger.exception("graph start_lesson failed for lesson %s", lesson.id)
        raise errors.internal_error("Could not initialise the lesson state") from exc
    if not isinstance(result, dict):
        raise errors.internal_error("Lesson state initialisation returned no state")

    state = result.get("state") or {}
    plan = result.get("plan") or {}
    _apply_state_to_lesson(lesson, state)
    if plan:
        meta = dict(lesson.session_metadata or {})
        concepts = plan.get("concepts") or []
        if concepts and not lesson.current_concept:
            first = concepts[0]
            if isinstance(first, dict) and first.get("id"):
                lesson.current_concept = str(first["id"])
                meta["current_concept_title"] = first.get("title")
        lesson.session_metadata = meta
    # The graph service owns the lesson_plans row; only make sure one exists.
    plan_row = await get_lesson_plan(db, lesson.id)
    if plan_row is None and plan:
        logger.warning(
            "graph service did not persist lesson_plans for lesson %s; "
            "detail responses will use an empty plan until it does",
            lesson.id,
        )
    await db.flush()
    return lesson


def _apply_state_to_lesson(lesson: Lesson, state: dict) -> None:
    """Copy denormalised resume fields from graph state onto the lesson row."""
    if not isinstance(state, dict):
        return
    status = state.get("status")
    if isinstance(status, str) and status in LessonStatus.ALL:
        lesson.status = status
    if state.get("current_concept"):
        lesson.current_concept = str(state["current_concept"])
    progress = state.get("progress")
    if progress is not None:
        try:
            lesson.progress = max(0.0, min(1.0, float(progress)))
        except (TypeError, ValueError):
            pass
    for field in ("board_revision", "last_board_sequence", "elapsed_seconds"):
        value = state.get(field)
        if value is not None:
            try:
                setattr(lesson, field, max(0, int(value)))
            except (TypeError, ValueError):
                pass
    lesson.updated_at = utcnow()


# ---------------------------------------------------------------------------
# list / detail
# ---------------------------------------------------------------------------


async def list_lessons(
    db: AsyncSession,
    student: User,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dtos.LessonListResponse:
    if status is not None and status not in LessonStatus.ALL:
        raise errors.invalid_arguments(
            f"status must be one of {sorted(LessonStatus.ALL)}",
            details={"field": "status", "allowed": sorted(LessonStatus.ALL)},
        )
    lessons, total = await list_lesson_rows(
        db, student.id, status=status, limit=limit, offset=offset
    )
    lesson_ids = [lesson.id for lesson in lessons]
    transcript_map = await transcript_presence_map(db, lesson_ids)
    notes_map = await notes_presence_map(db, lesson_ids)
    items = [
        lesson_summary(
            lesson,
            has_transcript=transcript_map.get(lesson.id, 0) > 0,
            has_notes=notes_map.get(lesson.id, 0) > 0,
        )
        for lesson in lessons
    ]
    return dtos.LessonListResponse(
        items=items, total=int(total), limit=int(limit), offset=int(offset)
    )


# ---------------------------------------------------------------------------
# lifecycle transitions
# ---------------------------------------------------------------------------


async def touch_lesson(db: AsyncSession, lesson: Lesson) -> Lesson:
    lesson.last_activity_at = utcnow()
    await db.flush()
    return lesson


async def update_lesson_state(
    db: AsyncSession,
    lesson: Lesson,
    *,
    status: str | None = None,
    current_concept: str | None = None,
    progress: float | None = None,
    board_revision: int | None = None,
    last_board_sequence: int | None = None,
    elapsed_seconds: int | None = None,
    extra_metadata: dict | None = None,
) -> Lesson:
    fields: dict = {}
    if status is not None:
        if status not in LessonStatus.ALL:
            raise errors.invalid_arguments(f"unknown lesson status: {status}")
        fields["status"] = status
    if current_concept is not None:
        fields["current_concept"] = current_concept
    if progress is not None:
        fields["progress"] = max(0.0, min(1.0, float(progress)))
    if board_revision is not None:
        fields["board_revision"] = max(0, int(board_revision))
    if last_board_sequence is not None:
        fields["last_board_sequence"] = max(0, int(last_board_sequence))
    if elapsed_seconds is not None:
        fields["elapsed_seconds"] = max(0, int(elapsed_seconds))
    if extra_metadata:
        meta = dict(lesson.session_metadata or {})
        meta.update(extra_metadata)
        fields["session_metadata"] = meta
    fields["last_activity_at"] = utcnow()
    return await update_lesson_row(db, lesson, **fields)


async def resume_lesson(
    db: AsyncSession, lesson: Lesson
) -> tuple[Lesson, dtos.BoardSnapshotEnvelope, int, bool]:
    """Re-open a lesson for a live session.

    Returns ``(lesson, board_snapshot, transcript_cursor, needs_notes_prompt)``.
    The notes prompt is evaluated against the state *before* re-opening, so a
    finished lesson that was never turned into notes still asks the student.
    """
    prompt = await needs_notes_prompt(db, lesson)
    state = await graph_service.load_lesson_state(lesson.id)
    if state is None:
        # Checkpoint missing (fresh dev DB / wiped LangGraph store): re-create
        # the deterministic initial state. start_lesson is idempotent.
        logger.info("re-initialising graph state for lesson %s", lesson.id)
        try:
            await graph_service.start_lesson(db, lesson)
        except Exception as exc:
            logger.exception("graph start_lesson (resume) failed for lesson %s", lesson.id)
            raise errors.internal_error("Could not restore the lesson state") from exc
    else:
        _apply_state_to_lesson(lesson, state)

    now = utcnow()
    lesson.last_activity_at = now
    if lesson.status in ENDED_STATUSES:
        # "Continue Live Session": reopen the session; keep ended_at cleared.
        lesson.status = LessonStatus.LIVE
        lesson.ended_at = None
    elif lesson.status in RESUMABLE_STATUSES:
        lesson.status = LessonStatus.LIVE
        if lesson.started_at is None:
            lesson.started_at = now
    if lesson.started_at is None:
        lesson.started_at = now
    # Sync board_revision from the append-only event log so the lesson row
    # reflects the actual board state (graph state may carry a stale value).
    from sqlalchemy import func, select
    from app.db.models import LessonBoardEvent

    max_rev = (
        await db.execute(
            select(func.max(LessonBoardEvent.board_revision)).where(
                LessonBoardEvent.lesson_id == lesson.id
            )
        )
    ).scalar_one_or_none()
    if max_rev:
        lesson.board_revision = max(int(lesson.board_revision or 0), int(max_rev))
    await db.flush()

    board = await get_board_snapshot(db, lesson)
    cursor = await get_transcript_cursor(db, lesson.id)
    return lesson, board, cursor, prompt


async def end_lesson(
    db: AsyncSession, lesson: Lesson, reason: str | None = None
) -> Lesson:
    """Finish the lesson: generate the final summary, persist lifecycle columns."""
    if lesson.status in ENDED_STATUSES and lesson.ended_at is not None:
        return lesson
    try:
        result = await graph_service.finish_lesson(
            db, lesson.id, reason=reason or "student_ended"
        )
    except Exception as exc:
        logger.exception("graph finish_lesson failed for lesson %s", lesson.id)
        raise errors.internal_error("Could not finish the lesson") from exc
    state = result if isinstance(result, dict) else {}
    now = utcnow()
    _apply_state_to_lesson(lesson, state)

    lesson.status = LessonStatus.COMPLETED
    lesson.ended_at = now
    lesson.last_activity_at = now
    lesson.notes_prompted = False
    meta = dict(lesson.session_metadata or {})
    if reason:
        meta["end_reason"] = reason
    summary = state.get("summary") or state.get("assessment_summary")
    if isinstance(summary, str) and summary.strip():
        meta["assessment_summary"] = summary.strip()
    lesson.session_metadata = meta
    started = ensure_utc(lesson.started_at)
    if started is not None:
        # When finishing, elapsed = full duration so remaining_seconds is 0.
        lesson.elapsed_seconds = int(lesson.duration_minutes or 0) * 60
    else:
        lesson.elapsed_seconds = 0
    await db.flush()
    return lesson


async def needs_notes_prompt(db: AsyncSession, lesson: Lesson) -> bool:
    """True when a finished transcript-only lesson should offer note generation."""
    ended = lesson.status in ENDED_STATUSES or lesson.ended_at is not None
    if not ended or lesson.notes_prompted:
        return False
    if not await lesson_has_transcript(db, lesson.id):
        return False
    if await lesson_has_notes(db, lesson.id):
        return False
    return True


async def mark_notes_prompted(db: AsyncSession, lesson: Lesson) -> None:
    if not lesson.notes_prompted:
        lesson.notes_prompted = True
        lesson.updated_at = utcnow()
        await db.flush()


# ---------------------------------------------------------------------------
# transcript helpers for routers
# ---------------------------------------------------------------------------


async def list_transcript(
    db: AsyncSession, lesson_id: str
) -> list[dtos.TranscriptEntry]:
    from app.contracts.mappers import transcript_entry_to_dto

    rows = await fetch_entries(db, lesson_id)
    return [transcript_entry_to_dto(row) for row in rows]


async def board_events(
    db: AsyncSession, lesson_id: str, *, after_sequence: int = 0
) -> list[dtos.BoardEvent]:
    from app.contracts.mappers import board_event_to_dto

    rows = await board_events_after(db, lesson_id, after_sequence=after_sequence)
    return [board_event_to_dto(row) for row in rows]


__all__ = [
    "board_events",
    "build_lesson_detail",
    "create_lesson",
    "end_lesson",
    "get_board_snapshot",
    "list_lessons",
    "list_transcript",
    "mark_notes_prompted",
    "needs_notes_prompt",
    "resume_lesson",
    "touch_lesson",
    "update_lesson_state",
]
