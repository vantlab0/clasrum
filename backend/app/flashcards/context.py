"""Flashcard context — everything the flashcard generators are allowed to read."""
from __future__ import annotations

from dataclasses import dataclass

from app.contracts import dtos
from app.db.models import Lesson


@dataclass
class FlashcardContext:
    lesson: Lesson
    plan: dtos.LessonPlan
    transcript: list[dict[str, Any]]
    board_elements: list[dict[str, Any]]