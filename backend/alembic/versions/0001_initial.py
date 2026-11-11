"""Baseline schema for the AI Personal Classroom backend.

Hand-written equivalent of ``alembic revision --autogenerate`` against
``app.db.models`` (docs/CONTRACTS.md §8). Portable across SQLite and
PostgreSQL: JSON columns map to JSON, timestamptz is used on PostgreSQL, and
every constraint/index keeps the name declared on the ORM model.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ users
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("default_level", sa.String(length=32), nullable=False),
        sa.Column("default_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ----------------------------------------------------- learning profiles
    op.create_table(
        "student_learning_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("subjects_studied", sa.JSON(), nullable=False),
        sa.Column("preferred_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id"),
    )

    # -------------------------------------------------------- concept mastery
    op.create_table(
        "student_concept_mastery",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("concept", sa.String(length=240), nullable=False),
        sa.Column("mastery_level", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("correct_answers", sa.Integer(), nullable=False),
        sa.Column("misconceptions", sa.JSON(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_id", "subject", "concept", name="uq_mastery_student_subject_concept"
        ),
    )
    op.create_index(
        "ix_mastery_student_subject",
        "student_concept_mastery",
        ["student_id", "subject"],
    )

    # ---------------------------------------------------------------- lessons
    op.create_table(
        "lessons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=True),
        sa.Column("learning_level", sa.String(length=32), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_concept", sa.String(length=240), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("board_revision", sa.Integer(), nullable=False),
        sa.Column("last_board_sequence", sa.Integer(), nullable=False),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes_prompted", sa.Boolean(), nullable=False),
        sa.Column("session_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_student_status", "lessons", ["student_id", "status"])

    # ------------------------------------------------------------ lesson plans
    op.create_table(
        "lesson_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("objectives", sa.JSON(), nullable=False),
        sa.Column("concepts", sa.JSON(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lesson_id"),
    )

    # ------------------------------------------------------- lesson checkpoints
    op.create_table(
        "lesson_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("langgraph_thread_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lesson_id"),
    )

    # -------------------------------------------------------- lesson transcripts
    op.create_table(
        "lesson_transcripts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lesson_id"),
    )

    # -------------------------------------------------- lesson transcript turns
    op.create_table(
        "lesson_transcript_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transcript_id"], ["lesson_transcripts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lesson_id", "sequence", name="uq_transcript_lesson_seq"),
    )

    # ------------------------------------------------------- lesson board events
    op.create_table(
        "lesson_board_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("element_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("board_revision", sa.Integer(), nullable=False),
        sa.Column("depends_on", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("animate", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lesson_id", "sequence", name="uq_board_event_lesson_seq"),
        sa.UniqueConstraint("event_id", name="uq_board_event_event_id"),
    )
    op.create_index(
        "ix_board_event_lesson_seq",
        "lesson_board_events",
        ["lesson_id", "sequence"],
    )

    # ---------------------------------------------------- lesson board snapshots
    op.create_table(
        "lesson_board_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lesson_id", "revision", name="uq_board_snapshot_lesson_rev"
        ),
    )

    # ----------------------------------------------------------- lesson assessments
    op.create_table(
        "lesson_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("concept", sa.String(length=240), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=True),
        sa.Column("student_answer", sa.Text(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assessment_lesson", "lesson_assessments", ["lesson_id"])

    # ---------------------------------------------------------------- lesson notes
    op.create_table(
        "lesson_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("source_transcript_id", sa.String(length=36), nullable=True),
        sa.Column("board_reference", sa.JSON(), nullable=False),
        sa.Column("generated_by", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_lesson", "lesson_notes", ["lesson_id"])

    # -------------------------------------------------- learning observations
    op.create_table(
        "student_learning_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("concept", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observation_student",
        "student_learning_observations",
        ["student_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_observation_student", table_name="student_learning_observations")
    op.drop_table("student_learning_observations")
    op.drop_index("ix_note_lesson", table_name="lesson_notes")
    op.drop_table("lesson_notes")
    op.drop_index("ix_assessment_lesson", table_name="lesson_assessments")
    op.drop_table("lesson_assessments")
    op.drop_table("lesson_board_snapshots")
    op.drop_index("ix_board_event_lesson_seq", table_name="lesson_board_events")
    op.drop_table("lesson_board_events")
    op.drop_table("lesson_transcript_entries")
    op.drop_table("lesson_transcripts")
    op.drop_table("lesson_checkpoints")
    op.drop_table("lesson_plans")
    op.drop_index("ix_lesson_student_status", table_name="lessons")
    op.drop_table("lessons")
    op.drop_index("ix_mastery_student_subject", table_name="student_concept_mastery")
    op.drop_table("student_concept_mastery")
    op.drop_table("student_learning_profiles")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
