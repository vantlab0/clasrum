"""Structured notes list/detail endpoints (§5)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.contracts import dtos
from app.contracts.mappers import note_to_detail, note_to_summary
from app.core import errors
from app.core.deps import CurrentStudent, DbSession
from app.db import repositories

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("", response_model=dtos.NotesListResponse)
async def list_notes(
    student: CurrentStudent,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dtos.NotesListResponse:
    notes, total = await repositories.list_notes_for_student(
        db, student.id, limit=limit, offset=offset
    )
    return dtos.NotesListResponse(
        items=[note_to_summary(note) for note in notes],
        total=int(total),
        limit=int(limit),
        offset=int(offset),
    )


@router.get("/{note_id}", response_model=dtos.NoteDetail)
async def get_note(
    note_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.NoteDetail:
    note = await repositories.get_note(db, note_id)
    if note is None:
        raise errors.not_found("Note not found")
    if note.student_id != student.id:
        raise errors.forbidden("Note belongs to another student")
    return note_to_detail(note)
