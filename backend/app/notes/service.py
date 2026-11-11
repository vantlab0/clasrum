"""Notes service: consent-gated request, background generation, persistence.

Flow (docs/CONTRACTS.md §5):
``POST /lessons/{id}/notes/generate {consent: true}`` validates that a
transcript exists, creates a ``pending`` ``lesson_notes`` row and returns it
immediately; generation runs in a FastAPI background task with its own DB
session. Success flips the row to ``ready``; any failure flips it to ``failed``
with ``error`` while the transcript and board remain intact.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts.mappers import plan_to_dto
from app.core import errors
from app.db.base import utcnow
from app.db.models import Lesson, LessonNote
from app.db.repositories import (
    create_note,
    fetch_transcript_entries,
    get_lesson,
    get_lesson_plan,
    get_note,
    get_transcript,
    list_assessments,
    pending_note_for_lesson,
)
from app.db.session import session_scope
from app.notes.board_context import load_board_elements
from app.notes.generator import NoteContext, generate

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 2000


def default_note_title(lesson: Lesson) -> str:
    return f"{lesson.topic} — Lesson Notes"


async def request_note_generation(
    db: AsyncSession, lesson: Lesson
) -> LessonNote:
    """Validate + create the pending note row (caller owns the transaction)."""
    entries = await fetch_transcript_entries(db, lesson.id)
    if not entries:
        raise errors.conflict(
            "This lesson has no transcript yet, so structured notes cannot be generated",
            details={"reason": "transcript_missing", "lesson_id": lesson.id},
        )
    existing_pending = await pending_note_for_lesson(db, lesson.id)
    if existing_pending is not None:
        return existing_pending
    transcript = await get_transcript(db, lesson.id)
    note = await create_note(
        db,
        lesson=lesson,
        title=default_note_title(lesson),
        source_transcript_id=transcript.id if transcript is not None else None,
        status="pending",
    )
    # The prompt has been answered by the student (consent given); do not ask
    # again when the lesson is resumed.
    lesson.notes_prompted = True
    lesson.updated_at = utcnow()
    await db.flush()
    return note


async def build_context(db: AsyncSession, note: LessonNote, lesson: Lesson) -> NoteContext:
    plan_row = await get_lesson_plan(db, lesson.id)
    return NoteContext(
        lesson=lesson,
        plan=plan_to_dto(plan_row),
        transcript=await fetch_transcript_entries(db, lesson.id),
        board_elements=await load_board_elements(db, lesson.id),
        assessments=await list_assessments(db, lesson.id),
    )


async def run_generation(note_id: str) -> None:
    """Background task: generate notes for ``note_id`` and persist the result.

    Never raises: a failure is recorded on the note row so the client can show
    a retry affordance without the process logging an unhandled task error.
    """
    settings = get_settings()
    generator_name = (
        f"openai:{settings.notes_model}"
        if settings.effective_notes_api_key
        else "offline:deterministic"
    )
    try:
        async with session_scope() as db:
            note = await get_note(db, note_id)
            if note is None or note.status != "pending":
                return
            lesson = await get_lesson(db, note.lesson_id)
            if lesson is None:
                note.status = "failed"
                note.error = "Lesson no longer exists"
                note.updated_at = utcnow()
                await db.flush()
                return
            try:
                context = await build_context(db, note, lesson)
                result = await generate(context, settings)
            except Exception as exc:  # noqa: BLE001 - persisted failure path
                logger.warning("notes generation failed for note %s: %s", note_id, exc)
                note.status = "failed"
                note.error = str(exc)[:MAX_ERROR_CHARS] or "notes generation failed"
                note.generated_by = generator_name
                note.updated_at = utcnow()
                await db.flush()
                return
            note.title = result.title
            note.status = "ready"
            note.content_markdown = result.content_markdown
            note.sections = result.note_sections()
            note.board_reference = result.board_reference_json()
            note.generated_by = result.generated_by
            note.error = None
            note.updated_at = utcnow()
            lesson.notes_prompted = True
            lesson.updated_at = utcnow()
            await db.flush()
            logger.info("notes generated for lesson %s (note %s)", lesson.id, note.id)
    except Exception:  # pragma: no cover - last-resort guard for background task
        logger.exception("notes generation task crashed for note %s", note_id)


__all__ = [
    "build_context",
    "default_note_title",
    "request_note_generation",
    "run_generation",
]
