"""Flashcard → DTO mappers and DB model conversions."""
from __future__ import annotations

from datetime import datetime, timezone

from app.contracts import dtos
from app.db.models import LessonFlashcard, LessonFlashcardSet


def flashcard_to_dto(
    flashcard: LessonFlashcard, flashcard_set: LessonFlashcardSet
) -> dtos.Flashcard:
    """Map one persisted flashcard row to the wire DTO."""
    return dtos.Flashcard(
        id=flashcard.id,
        topic=flashcard_set.topic,
        subject=flashcard_set.subject,
        class_level=flashcard_set.class_level,
        question=flashcard.question,
        answer=flashcard.answer,
        explanation=flashcard.explanation,
        order=flashcard.order,
    )


def _iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def set_to_summary(
    flashcard_set: LessonFlashcardSet, *, flashcard_count: int | None = None
) -> dtos.FlashcardSetSummary:
    """Map a LessonFlashcardSet to a FlashcardSetSummary DTO."""
    count = (
        flashcard_count
        if flashcard_count is not None
        else len(flashcard_set.flashcards)
    )
    return dtos.FlashcardSetSummary(
        id=flashcard_set.id,
        topic=flashcard_set.topic,
        subject=flashcard_set.subject,
        class_level=flashcard_set.class_level,
        status=flashcard_set.status,
        flashcard_count=count,
        created_at=flashcard_set.created_at,
    )


def set_to_detail(
    flashcard_set: LessonFlashcardSet,
    flashcards: list[LessonFlashcard] | None = None,
) -> dtos.FlashcardSet:
    """Map a LessonFlashcardSet and its ordered cards to the detail DTO."""
    rows = (
        list(flashcards)
        if flashcards is not None
        else list(flashcard_set.flashcards)
    )
    rows.sort(key=lambda card: (card.order, card.id))
    return dtos.FlashcardSet(
        id=flashcard_set.id,
        topic=flashcard_set.topic,
        subject=flashcard_set.subject,
        class_level=flashcard_set.class_level,
        status=flashcard_set.status,
        error=flashcard_set.error,
        created_at=flashcard_set.created_at,
        flashcards=[
            flashcard_to_dto(flashcard, flashcard_set) for flashcard in rows
        ],
    )