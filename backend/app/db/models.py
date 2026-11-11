"""Database models — logical schema per docs/CONTRACTS.md §8.

Ownership rules:
* `lessons.student_id` is the ownership root: every child row is scoped through it.
* Board events + snapshots are append-only (no updates to event rows).
* LangGraph checkpoints (managed by the library) stay authoritative for teaching
  state; `lesson_checkpoints` here is only a resume index.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin, new_id, utcnow


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="Student", nullable=False)
    default_level: Mapped[str] = mapped_column(String(32), default="high_school", nullable=False)
    default_duration_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped["StudentLearningProfile | None"] = relationship(
        back_populates="student", uselist=False, cascade="all, delete-orphan"
    )
    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )


class StudentLearningProfile(IdMixin, TimestampMixin, Base):
    __tablename__ = "student_learning_profiles"

    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    level: Mapped[str] = mapped_column(String(32), default="high_school", nullable=False)
    subjects_studied: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preferred_duration_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    student: Mapped[User] = relationship(back_populates="profile")


class StudentConceptMastery(IdMixin, TimestampMixin, Base):
    __tablename__ = "student_concept_mastery"
    __table_args__ = (
        UniqueConstraint("student_id", "subject", "concept", name="uq_mastery_student_subject_concept"),
        Index("ix_mastery_student_subject", "student_id", "subject"),
    )

    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    concept: Mapped[str] = mapped_column(String(240), nullable=False)
    mastery_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0..5
    status: Mapped[str] = mapped_column(String(32), default="not_started", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correct_answers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    misconceptions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentLearningObservation(IdMixin, Base):
    __tablename__ = "student_learning_observations"
    __table_args__ = (Index("ix_observation_student", "student_id", "created_at"),)

    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lesson_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    concept: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(48), default="observation", nullable=False)
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Lesson(IdMixin, TimestampMixin, Base):
    __tablename__ = "lessons"
    __table_args__ = (Index("ix_lesson_student_status", "student_id", "status"),)

    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(120), nullable=True)
    learning_level: Mapped[str] = mapped_column(String(32), default="high_school", nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="created", nullable=False)
    current_concept: Mapped[str | None] = mapped_column(String(240), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    board_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_board_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes_prompted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    student: Mapped[User] = relationship(back_populates="lessons")
    plan: Mapped["LessonPlan | None"] = relationship(
        back_populates="lesson", uselist=False, cascade="all, delete-orphan"
    )
    transcript: Mapped["LessonTranscript | None"] = relationship(
        back_populates="lesson", uselist=False, cascade="all, delete-orphan"
    )
    notes: Mapped[list["LessonNote"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )


class LessonPlan(IdMixin, TimestampMixin, Base):
    __tablename__ = "lesson_plans"

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    objectives: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    concepts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="plan")


class LessonCheckpoint(IdMixin, TimestampMixin, Base):
    __tablename__ = "lesson_checkpoints"

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    langgraph_thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class LessonTranscript(IdMixin, TimestampMixin, Base):
    __tablename__ = "lesson_transcripts"

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    entry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    lesson: Mapped[Lesson] = relationship(back_populates="transcript")
    entries: Mapped[list["LessonTranscriptEntry"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan", order_by="LessonTranscriptEntry.sequence"
    )


class LessonTranscriptEntry(IdMixin, Base):
    __tablename__ = "lesson_transcript_entries"
    __table_args__ = (UniqueConstraint("lesson_id", "sequence", name="uq_transcript_lesson_seq"),)

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lesson_transcripts.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)  # tutor|student|system
    text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcript: Mapped[LessonTranscript] = relationship(back_populates="entries")


class LessonBoardEvent(IdMixin, Base):
    __tablename__ = "lesson_board_events"
    __table_args__ = (
        UniqueConstraint("lesson_id", "sequence", name="uq_board_event_lesson_seq"),
        UniqueConstraint("event_id", name="uq_board_event_event_id"),
        Index("ix_board_event_lesson_seq", "lesson_id", "sequence"),
    )

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    element_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    board_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="qwen_tool", nullable=False)
    animate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LessonBoardSnapshot(IdMixin, Base):
    __tablename__ = "lesson_board_snapshots"
    __table_args__ = (
        UniqueConstraint("lesson_id", "revision", name="uq_board_snapshot_lesson_rev"),
    )

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LessonAssessment(IdMixin, Base):
    __tablename__ = "lesson_assessments"
    __table_args__ = (Index("ix_assessment_lesson", "lesson_id"),)

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    concept: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LessonNote(IdMixin, TimestampMixin, Base):
    __tablename__ = "lesson_notes"
    __table_args__ = (Index("ix_note_lesson", "lesson_id"),)

    lesson_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    source_transcript_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    board_reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    lesson: Mapped[Lesson] = relationship(back_populates="notes")
