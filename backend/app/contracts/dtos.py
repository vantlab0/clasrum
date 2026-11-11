"""Pydantic v2 wire DTOs — the exact REST surface from docs/CONTRACTS.md §5.

Every key is ``snake_case``; timestamps serialise as RFC 3339 UTC with
milliseconds and a trailing ``Z``. Request DTOs validate shape while semantic
rules (password strength, consent, ownership) live in the routers/services so
they can raise the stable §7 error envelope.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

#: Pragmatic RFC 5321-ish check; ``email-validator`` is not a declared
#: dependency of this project so we validate locally.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")


def _iso_z(value: datetime | None) -> str | None:
    """RFC 3339 UTC with exactly millisecond precision and a ``Z`` suffix."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


UTCDateTime = Annotated[
    datetime, PlainSerializer(_iso_z, return_type=str, when_used="json")
]
OptUTCDateTime = Annotated[
    datetime | None, PlainSerializer(_iso_z, return_type=str | None, when_used="json")
]


class WireModel(BaseModel):
    """Base for all wire models; extras are rejected on requests, ignored on rows."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


def normalize_email(value: str) -> str:
    return value.strip().lower()


# ---------------------------------------------------------------------------
# health / errors
# ---------------------------------------------------------------------------


class HealthResponse(WireModel):
    status: str
    version: str
    db: str
    qwen_configured: bool


class ErrorBody(WireModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False


class ErrorEnvelope(WireModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# auth / student
# ---------------------------------------------------------------------------


class Student(WireModel):
    id: str
    email: str
    display_name: str
    default_level: str
    default_duration_minutes: int
    preferences: dict[str, Any]
    created_at: UTCDateTime


class RegisterRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = normalize_email(value)
        if not _EMAIL_RE.match(value):
            raise ValueError("must be a valid email address")
        return value

    @field_validator("display_name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class LoginRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        value = normalize_email(value)
        if not _EMAIL_RE.match(value):
            raise ValueError("must be a valid email address")
        return value


class TokenResponse(WireModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    student: Student


class UpdateMeRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    default_level: str | None = Field(default=None, min_length=1, max_length=32)
    default_duration_minutes: int | None = Field(default=None, ge=1, le=600)
    preferences: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def _trim_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class DeleteDataRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    confirm: bool = False

    @field_validator("confirm", mode="before")
    @classmethod
    def _confirm_must_be_bool(cls, value: Any) -> Any:
        # Reject truthy strings ("true", "yes", "1") that Pydantic would
        # otherwise coerce to bool True — a stray request must never wipe data.
        if isinstance(value, str):
            raise ValueError("confirm must be a boolean true, not a string")
        return value


class DeleteDataResponse(WireModel):
    deleted: bool = True


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------


class CreateLessonRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    topic: str = Field(min_length=2, max_length=500)
    learning_level: str = Field(default="high_school", min_length=1, max_length=32)
    duration_minutes: int = Field(ge=1, le=600)
    subject: str | None = Field(default=None, max_length=120)

    @field_validator("topic")
    @classmethod
    def _trim_topic(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("topic must be at least 2 characters")
        return value

    @field_validator("subject")
    @classmethod
    def _trim_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EndLessonRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


class PlanConcept(WireModel):
    id: str
    title: str
    order: int = 0
    status: str = "pending"
    objectives: list[str] = Field(default_factory=list)


class LessonPlan(WireModel):
    objectives: list[str] = Field(default_factory=list)
    concepts: list[PlanConcept] = Field(default_factory=list)


class TeachingState(WireModel):
    current_concept_id: str | None = None
    current_concept_title: str | None = None
    completed_concept_ids: list[str] = Field(default_factory=list)
    pending_concept_ids: list[str] = Field(default_factory=list)
    progress: float = 0.0
    understanding_score: float = 0.0
    assessment_results: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str | None = None
    is_finished: bool = False


class LessonSummary(WireModel):
    id: str
    topic: str
    subject: str | None = None
    status: str
    learning_level: str
    duration_minutes: int
    progress: float
    current_concept: str | None = None
    started_at: OptUTCDateTime = None
    last_activity_at: OptUTCDateTime = None
    ended_at: OptUTCDateTime = None
    has_transcript: bool = False
    has_notes: bool = False
    board_revision: int = 0


class LessonDetail(LessonSummary):
    plan: LessonPlan = Field(default_factory=LessonPlan)
    teaching_state: TeachingState = Field(default_factory=TeachingState)
    elapsed_seconds: int = 0
    remaining_seconds: int = 0
    assessment_summary: str | None = None


class LessonListResponse(WireModel):
    items: list[LessonSummary] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------


class Position(WireModel):
    x: float
    y: float


class Size(WireModel):
    w: float
    h: float


class BoardElement(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    element_id: str
    type: str
    level: int = 2
    payload: dict[str, Any] = Field(default_factory=dict)
    position: Position | None = None
    size: Size | None = None
    style: str | None = None
    color: str | None = None
    created_sequence: int = 0
    updated_sequence: int | None = None
    removed_at_sequence: int | None = None
    animation: bool | dict[str, Any] | None = None


class BoardDocument(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    session_id: str
    revision: int = 0
    last_sequence: int = 0
    elements: list[BoardElement] = Field(default_factory=list)
    created_at: OptUTCDateTime = None
    updated_at: OptUTCDateTime = None


class BoardEvent(WireModel):
    event_id: str
    session_id: str
    sequence: int
    type: str
    element_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: UTCDateTime
    board_revision: int = 0
    depends_on: list[str] = Field(default_factory=list)
    source: str = "qwen_tool"
    animate: bool = True


class BoardSnapshotEnvelope(WireModel):
    session_id: str
    revision: int
    last_sequence: int
    snapshot: BoardDocument | None = None
    snapshot_at: OptUTCDateTime = None
    board_events: list[BoardEvent] = Field(default_factory=list)


class BoardEventsResponse(WireModel):
    events: list[BoardEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# transcript / notes
# ---------------------------------------------------------------------------


class TranscriptEntry(WireModel):
    id: str
    session_id: str
    sequence: int
    speaker: Literal["tutor", "student", "system"]
    text: str
    started_at: UTCDateTime
    ended_at: OptUTCDateTime = None


class TranscriptResponse(WireModel):
    entries: list[TranscriptEntry] = Field(default_factory=list)


class NoteSection(WireModel):
    heading: str
    body: str
    latex: str | None = None


class NoteSummary(WireModel):
    id: str
    lesson_id: str
    title: str
    subject: str | None = None
    status: Literal["pending", "ready", "failed"]
    created_at: UTCDateTime
    updated_at: UTCDateTime
    word_count: int = 0


class NoteDetail(NoteSummary):
    content_markdown: str | None = None
    sections: list[NoteSection] = Field(default_factory=list)
    source_transcript_id: str | None = None
    generated_by: str | None = None
    error: str | None = None


class LessonNotesResponse(WireModel):
    notes: list[NoteSummary] = Field(default_factory=list)


class NotesListResponse(WireModel):
    items: list[NoteSummary] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class GenerateNotesRequest(WireModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    consent: bool = False

    @field_validator("consent", mode="before")
    @classmethod
    def _consent_must_be_bool(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError("consent must be a boolean true, not a string")
        return value


class GenerateNotesResponse(WireModel):
    note: NoteDetail
    status: Literal["pending", "ready", "failed"]


class ResumeResponse(WireModel):
    lesson: LessonDetail
    board: BoardSnapshotEnvelope
    transcript_cursor: int
    needs_notes_prompt: bool


# ---------------------------------------------------------------------------
# internal (non-wire) payloads
# ---------------------------------------------------------------------------


class LessonCreationResult(WireModel):
    lesson: LessonDetail
    plan: LessonPlan
