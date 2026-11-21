"""Lesson lifecycle, board, transcript, notes and flashcards REST endpoints (§5).

Every route below is lesson-scoped and goes through ``require_lesson_owner``
before touching any child table, so ownership is enforced in exactly one place.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, BackgroundTasks, Query

from app.contracts import dtos
from app.contracts.mappers import board_event_to_dto, note_to_detail, note_to_summary, transcript_entry_to_dto
from app.core import errors
from app.core.deps import CurrentStudent, DbSession, require_lesson_owner
from app.db import repositories
from app.lessons import service as lessons_service
from app.notes.service import request_note_generation, run_generation
from app.flashcards.mappers import set_to_detail, set_to_summary
from app.flashcards.service import request_flashcard_generation, run_flashcard_generation
from app.videos.service import search_videos, get_watch_history, record_watch
from app.quiz.service import generate_quiz, get_quiz_results

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


# ---------------------------------------------------------------------------
# flashcards
# ---------------------------------------------------------------------------


@router.get(
    "/{lesson_id}/flashcards",
    response_model=dtos.FlashcardSetListResponse,
)
async def list_flashcard_sets(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.FlashcardSetListResponse:
    """List previously generated flashcard sets for a lesson."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    sets = await repositories.list_flashcard_sets_for_lesson(db, lesson.id)
    items: list[dtos.FlashcardSetSummary] = []
    for flashcard_set in sets:
        cards = await repositories.list_flashcards_in_set(db, flashcard_set.id)
        items.append(
            set_to_summary(flashcard_set, flashcard_count=len(cards))
        )
    return dtos.FlashcardSetListResponse(
        items=items,
        total=len(items),
        limit=len(items) or 50,
        offset=0,
    )


@router.get(
    "/{lesson_id}/flashcards/{flashcard_set_id}",
    response_model=dtos.FlashcardSet,
)
async def get_flashcard_set(
    lesson_id: str,
    flashcard_set_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.FlashcardSet:
    """Read one persisted flashcard set, including every generated card."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    flashcard_set = await repositories.get_flashcard_set(db, flashcard_set_id)
    if flashcard_set is None or flashcard_set.lesson_id != lesson.id:
        raise errors.not_found(
            "Flashcard set not found",
            details={"flashcard_set_id": flashcard_set_id},
        )
    cards = await repositories.list_flashcards_in_set(db, flashcard_set.id)
    return set_to_detail(flashcard_set, cards)


@router.post(
    "/{lesson_id}/flashcards/generate",
    response_model=dtos.FlashcardSetSummary,
)
async def generate_flashcards(
    lesson_id: str,
    payload: dtos.CreateFlashcardSetRequest,
    background_tasks: BackgroundTasks,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.FlashcardSetSummary:
    """Generate flashcards from a lesson topic.

    Returns a summary ID immediately; the background task populates the
    flashcard set with AI-generated cards keyed by the set ID.
    """
    if len(payload.topic.strip()) < 2:
        raise errors.invalid_arguments(
            "topic must be at least 2 characters",
        )
    lesson = await require_lesson_owner(lesson_id, student, db)
    flashcard_set = await request_flashcard_generation(db, lesson, payload)
    # Commit before yielding to the background task so it can see the row from
    # its own session (FastAPI runs background tasks before dependency teardown).
    await db.commit()
    background_tasks.add_task(
        run_flashcard_generation,
        flashcard_set.id,
        payload.topic,
        payload.subject,
        payload.class_level,
    )
    cards = await repositories.list_flashcards_in_set(db, flashcard_set.id)
    return set_to_summary(flashcard_set, flashcard_count=len(cards))


# ---------------------------------------------------------------------------
# quiz
# ---------------------------------------------------------------------------


@router.post(
    "/{lesson_id}/quiz/generate",
    response_model=dtos.QuizGenerationResponse,
)
async def generate_quiz_endpoint(
    lesson_id: str,
    payload: dtos.QuizGenerationRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.QuizGenerationResponse:
    """Generate a multiple-choice quiz on the given topic."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    result = await generate_quiz(db, lesson, payload)
    return result


@router.post(
    "/{lesson_id}/quiz/results",
    response_model=dtos.QuizResultsResponse,
)
async def get_quiz_results_endpoint(
    lesson_id: str,
    payload: dtos.QuizResultsRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.QuizResultsResponse:
    """Calculate and return quiz results from submitted answers."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    result = await get_quiz_results(db, lesson, payload)
    return result


# ---------------------------------------------------------------------------
# videos
# ---------------------------------------------------------------------------


@router.post(
    "/{lesson_id}/videos/search",
    response_model=dtos.VideoSearchResponse,
)
async def search_videos_endpoint(
    lesson_id: str,
    payload: dtos.VideoSearchRequest,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.VideoSearchResponse:
    """Search for educational videos on a given topic.

    The AI may refine the query for better results, but preserves the original
    topic intent. Returns ranked videos with metadata.
    """
    lesson = await require_lesson_owner(lesson_id, student, db)
    result = await search_videos(db, lesson, payload)
    return result


@router.get(
    "/{lesson_id}/videos/history",
    response_model=dtos.VideoWatchHistoryResponse,
)
async def get_watch_history_endpoint(
    lesson_id: str,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.VideoWatchHistoryResponse:
    """Retrieve the student's watch history for videos."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    result = await get_watch_history(db, lesson)
    return result


@router.post(
    "/{lesson_id}/videos/watched",
    response_model=dtos.VideoWatchHistoryEntry,
)
async def record_watched_endpoint(
    lesson_id: str,
    payload: dtos.VideoWatchHistoryEntry,
    student: CurrentStudent,
    db: DbSession,
) -> dtos.VideoWatchHistoryEntry:
    """Record that a student watched a video."""
    lesson = await require_lesson_owner(lesson_id, student, db)
    result = await record_watch(db, lesson, payload)
    return result
