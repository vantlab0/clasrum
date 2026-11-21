"""Flashcards, video watch history/cache, and transcript entry nullability.

Hand-written equivalent of ``alembic revision --autogenerate`` against
``app.db.models`` after the flashcards/videos features landed.

Revision ID: 0002_flashcards_videos
Revises: 0001_initial
Create Date: 2026-11-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_flashcards_videos"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------- transcript entries
    op.alter_column(
        "lesson_transcript_entries",
        "started_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )

    # -------------------------------------------------- flashcard sets
    op.create_table(
        "lesson_flashcard_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=True),
        sa.Column("class_level", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flashcard_set_lesson", "lesson_flashcard_sets", ["lesson_id"])

    # ------------------------------------------------------ flashcards
    op.create_table(
        "lesson_flashcards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("flashcard_set_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.String(length=500), nullable=False),
        sa.Column("answer", sa.String(length=1000), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["flashcard_set_id"], ["lesson_flashcard_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flashcard_set", "lesson_flashcards", ["flashcard_set_id"])

    # --------------------------------------------------- watch history
    op.create_table(
        "video_watch_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("video_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=500), nullable=True),
        sa.Column("channel_title", sa.String(length=200), nullable=True),
        sa.Column("search_query", sa.String(length=500), nullable=False),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_watch_history_lesson_video",
        "video_watch_history",
        ["lesson_id", "video_id"],
    )
    op.create_index(
        "ix_watch_history_watched_at", "video_watch_history", ["watched_at"]
    )

    # ---------------------------------------------------- search cache
    op.create_table(
        "video_search_cache",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cache_key", sa.String(length=255), nullable=False),
        sa.Column("cached_data", sa.Text(), nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_cache_key", "video_search_cache", ["cache_key"], unique=True)
    op.create_index("ix_search_cache_cached_at", "video_search_cache", ["cached_at"])


def downgrade() -> None:
    op.drop_index("ix_search_cache_cached_at", table_name="video_search_cache")
    op.drop_index("ix_search_cache_key", table_name="video_search_cache")
    op.drop_table("video_search_cache")

    op.drop_index("ix_watch_history_watched_at", table_name="video_watch_history")
    op.drop_index("ix_watch_history_lesson_video", table_name="video_watch_history")
    op.drop_table("video_watch_history")

    op.drop_index("ix_flashcard_set", table_name="lesson_flashcards")
    op.drop_table("lesson_flashcards")

    op.drop_index("ix_flashcard_set_lesson", table_name="lesson_flashcard_sets")
    op.drop_table("lesson_flashcard_sets")

    op.alter_column(
        "lesson_transcript_entries",
        "started_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
