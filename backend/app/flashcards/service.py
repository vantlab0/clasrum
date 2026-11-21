"""Flashcard generation service — AI-powered flashcard creation for lessons.

Flow (analogous to notes service §5):
``POST /lessons/{lesson_id}/flashcards/generate`` with a topic creates a
``lesson_flashcard_set`` row keyed by a stable ID and returns a summary immediately;
a background task then calls the configured AI to populate the set with
question/answer cards grounded in the lesson content.

Two generators share one validated output shape:
* ``generate_with_llm`` — OpenAI-compatible chat completion asked for strict JSON.
* ``generate_offline`` — deterministic, fully local extraction from the lesson
  plan, concepts and board content so the product flow works with no API key.
Any failure records the set as ``failed`` with an error message; the client can
retry via the same endpoint.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts import dtos
from app.core import errors
from app.db.base import utcnow
from app.db.models import Lesson, LessonFlashcardSet, LessonFlashcard
from app.db.repositories import (
    create_flashcard_set,
    get_flashcard_set,
    list_flashcards_in_set,
    get_lesson,
)
from app.db.session import session_scope
from app.flashcards.context import FlashcardContext
from app.flashcards.generator import generate, generate_offline
from app.flashcards.mappers import set_to_summary, set_to_detail
from app.notes.board_context import element_heading, element_text

logger = logging.getLogger(__name__)


async def request_flashcard_generation(
    db: AsyncSession, lesson: Lesson, payload: dtos.CreateFlashcardSetRequest
) -> LessonFlashcardSet:
    """Validate + create the pending flashcard set row (caller owns the transaction)."""
    # Check lesson exists and is accessible
    db_lesson = await get_lesson(db, lesson.id)
    if db_lesson is None:
        raise errors.not_found("Lesson not found", details={"lesson_id": lesson.id})

    existing = await get_flashcard_set(db, lesson.id)
    if existing is not None and existing.status != "failed":
        return existing

    flashcard_set = await create_flashcard_set(
        db,
        lesson=lesson,
        topic=payload.topic,
        subject=payload.subject,
        class_level=payload.class_level,
        status="pending",
    )
    lesson.updated_at = utcnow()
    await db.flush()
    return flashcard_set


async def run_flashcard_generation(
    flashcard_set_id: str,
    topic: str,
    subject: str | None,
    class_level: str | None,
) -> None:
    """Background task: generate flashcards for ``flashcard_set_id`` and persist.

    Never raises: a failure is recorded on the set row so the client can show
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
            flashcard_set = await get_flashcard_set(db, flashcard_set_id)
            if flashcard_set is None or flashcard_set.status != "pending":
                return
            lesson = await get_lesson(db, flashcard_set.lesson_id)
            if lesson is None:
                flashcard_set.status = "failed"
                flashcard_set.error = "Lesson no longer exists"
                flashcard_set.updated_at = utcnow()
                await db.flush()
                return
            try:
                context = await _build_context(db, lesson)
                result = await generate(
                    context, settings, topic=topic, subject=subject,
                    class_level=class_level,
                )
            except Exception as exc:  # noqa: BLE001 - persisted failure path
                logger.warning(
                    "flashcard generation failed for set %s: %s", flashcard_set_id, exc
                )
                flashcard_set.status = "failed"
                flashcard_set.error = str(exc)[:2000] or "flashcard generation failed"
                flashcard_set.updated_at = utcnow()
                await db.flush()
                return
            # Persist the generated flashcards
            from app.db.models import LessonFlashcard
            for i, card in enumerate(result.cards):
                flashcard = LessonFlashcard(
                    flashcard_set_id=flashcard_set.id,
                    question=card.get("question", ""),
                    answer=card.get("answer", ""),
                    explanation=card.get("explanation"),
                    order=i,
                )
                db.add(flashcard)
            flashcard_set.status = "ready"
            flashcard_set.error = None
            flashcard_set.updated_at = utcnow()
            await db.flush()
            logger.info(
                "flashcards generated for lesson %s (set %s)", lesson.id, flashcard_set_id
            )
    except Exception:  # pragma: no cover - last-resort guard for background task
        logger.exception(
            "flashcard generation task crashed for set %s", flashcard_set_id
        )
        try:
            async with session_scope() as db:
                flashcard_set = await get_flashcard_set(db, flashcard_set_id)
                if flashcard_set is not None:
                    flashcard_set.status = "failed"
                    flashcard_set.error = "generation task crashed"
                    flashcard_set.updated_at = utcnow()
                    await db.flush()
        except Exception:
            pass


async def _build_context(db: AsyncSession, lesson: Lesson) -> FlashcardContext:
    from app.lessons.service import get_lesson_plan

    plan_row = await get_lesson_plan(db, lesson.id)
    from app.flashcards.generator import topic_hash  # noqa: F811 local
    transcript = await _fetch_transcript(db, lesson.id)
    from app.notes.board_context import load_board_elements
    board_elements = await load_board_elements(db, lesson.id)

    # Build plan DTO
    from app.contracts import dtos as dtos_mod
    plan_dto = dtos_mod.LessonPlan(
        objectives=plan_row.objectives if plan_row else [],
        concepts=plan_row.concepts if plan_row else [],
    )

    return FlashcardContext(
        lesson=lesson,
        plan=plan_dto,
        transcript=transcript,
        board_elements=board_elements,
    )


async def _fetch_transcript(db: AsyncSession, lesson_id: str) -> list[dict[str, Any]]:
    from app.notes.service import fetch_transcript_entries

    entries = await fetch_transcript_entries(db, lesson_id)
    return [
        {"speaker": e.speaker, "text": e.text, "sequence": e.sequence}
        for e in entries
    ]