"""Mappers: SQLAlchemy rows / graph state dicts -> wire DTOs.

The mappers are deliberately total functions: a lesson that predates a feature
(no plan row, no board snapshot, no graph state) still produces a valid DTO
rather than a 500.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.contracts import dtos
from app.db.models import (
    Lesson,
    LessonBoardEvent,
    LessonBoardSnapshot,
    LessonNote,
    LessonPlan,
    LessonTranscriptEntry,
    User,
)

# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat all naive values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def elapsed_seconds(lesson: Lesson, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    base = max(int(lesson.elapsed_seconds or 0), 0)
    started = ensure_utc(lesson.started_at)
    if started is None:
        return base
    end = ensure_utc(lesson.ended_at) or now
    if end <= started:
        return base
    return max(base, int((end - started).total_seconds()))


# ---------------------------------------------------------------------------
# student
# ---------------------------------------------------------------------------


def student_to_dto(user: User) -> dtos.Student:
    return dtos.Student(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        default_level=user.default_level,
        default_duration_minutes=user.default_duration_minutes,
        preferences=dict(user.preferences or {}),
        created_at=user.created_at,
    )


# ---------------------------------------------------------------------------
# lesson plan / teaching state
# ---------------------------------------------------------------------------


def plan_to_dto(plan: LessonPlan | dict[str, Any] | None) -> dtos.LessonPlan:
    if plan is None:
        return dtos.LessonPlan()
    if isinstance(plan, dict):
        objectives = plan.get("objectives") or []
        concepts_raw = plan.get("concepts") or []
    else:
        objectives = plan.objectives or []
        concepts_raw = plan.concepts or []
    concepts: list[dtos.PlanConcept] = []
    for index, raw in enumerate(concepts_raw):
        if not isinstance(raw, dict):
            continue
        concepts.append(
            dtos.PlanConcept(
                id=str(raw.get("id") or f"concept_{index + 1}"),
                title=str(raw.get("title") or raw.get("name") or f"Concept {index + 1}"),
                order=int(raw.get("order", index)),
                status=str(raw.get("status") or "pending"),
                objectives=[str(o) for o in (raw.get("objectives") or [])],
            )
        )
    concepts.sort(key=lambda c: c.order)
    return dtos.LessonPlan(
        objectives=[str(o) for o in objectives],
        concepts=concepts,
    )


def _concept_title(plan_dto: dtos.LessonPlan, concept_id: str | None) -> str | None:
    if not concept_id:
        return None
    for concept in plan_dto.concepts:
        if concept.id == concept_id:
            return concept.title
    return concept_id


def teaching_state_to_dto(
    state: dict[str, Any] | None,
    lesson: Lesson,
    plan_dto: dtos.LessonPlan,
) -> dtos.TeachingState:
    """Merge graph teaching state (authoritative when present) with the DB row.

    The DB lesson row is a denormalised resume index; graph state wins for any
    field it defines.
    """
    state = state or {}
    current_id = (
        state.get("current_concept_id")
        or state.get("current_concept")
        or lesson.current_concept
    )
    if current_id is not None:
        current_id = str(current_id)
    completed_ids = [
        str(c) for c in (state.get("completed_concept_ids") or state.get("completed_concepts") or [])
    ]
    pending_ids = [
        str(c) for c in (state.get("pending_concept_ids") or state.get("pending_concepts") or [])
    ]
    if not completed_ids and not pending_ids:
        completed_ids = [c.id for c in plan_dto.concepts if c.status == "completed"]
        pending_ids = [c.id for c in plan_dto.concepts if c.status != "completed"]
    progress = state.get("progress")
    if progress is None:
        progress = lesson.progress if lesson.progress is not None else 0.0
    understanding = state.get("understanding_score")
    if understanding is None:
        understanding = 0.0
    status = str(state.get("status") or lesson.status or "created")
    is_finished = bool(
        state.get("is_finished")
        or status in {"completed", "abandoned"}
        or lesson.status in {"completed", "abandoned"}
    )
    return dtos.TeachingState(
        current_concept_id=current_id,
        current_concept_title=_concept_title(plan_dto, current_id),
        completed_concept_ids=completed_ids,
        pending_concept_ids=pending_ids,
        progress=float(progress or 0.0),
        understanding_score=float(understanding or 0.0),
        assessment_results=list(state.get("assessment_results") or []),
        recommended_action=state.get("recommended_action"),
        is_finished=is_finished,
    )


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------


def lesson_summary(
    lesson: Lesson,
    *,
    has_transcript: bool = False,
    has_notes: bool = False,
) -> dtos.LessonSummary:
    return dtos.LessonSummary(
        id=lesson.id,
        topic=lesson.topic,
        subject=lesson.subject,
        status=lesson.status,
        learning_level=lesson.learning_level,
        duration_minutes=lesson.duration_minutes,
        progress=float(lesson.progress or 0.0),
        current_concept=lesson.current_concept,
        started_at=lesson.started_at,
        last_activity_at=lesson.last_activity_at,
        ended_at=lesson.ended_at,
        has_transcript=has_transcript,
        has_notes=has_notes,
        board_revision=int(lesson.board_revision or 0),
    )


def lesson_detail(
    lesson: Lesson,
    *,
    plan: LessonPlan | dict[str, Any] | None = None,
    teaching_state: dict[str, Any] | None = None,
    has_transcript: bool = False,
    has_notes: bool = False,
    assessment_summary: str | None = None,
    now: datetime | None = None,
) -> dtos.LessonDetail:
    plan_dto = plan_to_dto(plan)
    state_dto = teaching_state_to_dto(teaching_state, lesson, plan_dto)
    elapsed = elapsed_seconds(lesson, now=now)
    remaining = max(int(lesson.duration_minutes or 0) * 60 - elapsed, 0)
    summary = lesson_summary(
        lesson, has_transcript=has_transcript, has_notes=has_notes
    )
    return dtos.LessonDetail(
        **summary.model_dump(),
        plan=plan_dto,
        teaching_state=state_dto,
        elapsed_seconds=elapsed,
        remaining_seconds=remaining,
        assessment_summary=assessment_summary,
    )


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------


def board_event_to_dto(row: LessonBoardEvent) -> dtos.BoardEvent:
    return dtos.BoardEvent(
        event_id=row.event_id,
        session_id=row.lesson_id,
        sequence=int(row.sequence),
        type=row.type,
        element_id=row.element_id,
        payload=dict(row.payload or {}),
        created_at=row.created_at,
        board_revision=int(row.board_revision or 0),
        depends_on=list(row.depends_on or []),
        source=row.source or "qwen_tool",
        animate=bool(row.animate),
    )


def board_document_from_json(
    raw: dict[str, Any] | None,
    *,
    session_id: str,
    revision: int = 0,
    last_sequence: int = 0,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dtos.BoardDocument | None:
    """Normalise a persisted board document (list or id-keyed elements)."""
    if not raw:
        return None
    elements_raw = raw.get("elements") or []
    if isinstance(elements_raw, dict):
        normalised = []
        for element_id, element in elements_raw.items():
            if not isinstance(element, dict):
                continue
            element = dict(element)
            element.setdefault("element_id", element_id)
            normalised.append(element)
        elements_raw = normalised
    elements: list[dtos.BoardElement] = []
    for element in elements_raw:
        if not isinstance(element, dict):
            continue
        elements.append(dtos.BoardElement.model_validate(element))
    return dtos.BoardDocument(
        session_id=str(raw.get("session_id") or session_id),
        revision=int(raw.get("revision", revision) or 0),
        last_sequence=int(raw.get("last_sequence", last_sequence) or 0),
        elements=elements,
        created_at=raw.get("created_at") or created_at,
        updated_at=raw.get("updated_at") or updated_at,
    )


def snapshot_envelope(
    lesson: Lesson,
    snapshot_row: LessonBoardSnapshot | None,
    events: list[LessonBoardEvent] | None = None,
) -> dtos.BoardSnapshotEnvelope:
    if snapshot_row is not None:
        document = board_document_from_json(
            snapshot_row.document,
            session_id=lesson.id,
            revision=snapshot_row.revision,
            last_sequence=snapshot_row.last_sequence,
            updated_at=snapshot_row.created_at,
        )
        return dtos.BoardSnapshotEnvelope(
            session_id=lesson.id,
            revision=int(snapshot_row.revision),
            last_sequence=int(snapshot_row.last_sequence),
            snapshot=document,
            snapshot_at=snapshot_row.created_at,
            board_events=[board_event_to_dto(e) for e in (events or [])],
        )
    return dtos.BoardSnapshotEnvelope(
        session_id=lesson.id,
        revision=int(lesson.board_revision or 0),
        last_sequence=int(lesson.last_board_sequence or 0),
        snapshot=None,
        snapshot_at=None,
        board_events=[board_event_to_dto(e) for e in (events or [])],
    )


# ---------------------------------------------------------------------------
# transcript / notes
# ---------------------------------------------------------------------------


def transcript_entry_to_dto(row: LessonTranscriptEntry) -> dtos.TranscriptEntry:
    return dtos.TranscriptEntry(
        id=row.id,
        session_id=row.lesson_id,
        sequence=int(row.sequence),
        speaker=row.speaker,  # type: ignore[arg-type]
        text=row.text,
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def _word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.split())


def note_to_summary(note: LessonNote) -> dtos.NoteSummary:
    word_count = _word_count(note.content_markdown)
    if not word_count:
        for section in note.sections or []:
            if isinstance(section, dict):
                word_count += _word_count(str(section.get("body") or ""))
                word_count += _word_count(str(section.get("heading") or ""))
    return dtos.NoteSummary(
        id=note.id,
        lesson_id=note.lesson_id,
        title=note.title,
        subject=note.subject,
        status=note.status,  # type: ignore[arg-type]
        created_at=note.created_at,
        updated_at=note.updated_at,
        word_count=word_count,
    )


def note_to_detail(note: LessonNote) -> dtos.NoteDetail:
    sections: list[dtos.NoteSection] = []
    for section in note.sections or []:
        if not isinstance(section, dict):
            continue
        sections.append(
            dtos.NoteSection(
                heading=str(section.get("heading") or ""),
                body=str(section.get("body") or ""),
                latex=section.get("latex"),
            )
        )
    summary = note_to_summary(note)
    return dtos.NoteDetail(
        **summary.model_dump(),
        content_markdown=note.content_markdown,
        sections=sections,
        source_transcript_id=note.source_transcript_id,
        generated_by=note.generated_by,
        error=note.error,
    )
