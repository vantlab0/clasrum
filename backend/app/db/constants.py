"""Stable string enums shared across the backend."""
from __future__ import annotations


class LessonStatus:
    CREATED = "created"
    LIVE = "live"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    ERROR = "error"

    ALL = {CREATED, LIVE, PAUSED, COMPLETED, ABANDONED, ERROR}


class Speaker:
    TUTOR = "tutor"
    STUDENT = "student"
    SYSTEM = "system"


class NoteStatus:
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ConceptStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class BoardEventSource:
    QWEN_TOOL = "qwen_tool"
    SYSTEM = "system"
    STUDENT = "student"


CHALK_COLORS: dict[str, str] = {
    "primary": "#F2F0E8",
    "secondary": "#A8A79E",
    "blue": "#9CC9E3",
    "yellow": "#E8D58C",
    "red": "#E39C9C",
    "green": "#A9D3A0",
}
