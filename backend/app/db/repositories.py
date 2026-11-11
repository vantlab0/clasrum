"""Repository helpers — the only place raw SQLAlchemy queries are written.

Routers and services call these functions; every lesson-scoped query is rooted
at ``student_id`` (ownership root per docs/CONTRACTS.md §8) and every cascade
delete is explicit so behaviour is identical on SQLite and PostgreSQL.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import (
    Lesson,
    LessonAssessment,
    LessonBoardEvent,
    LessonBoardSnapshot,
    LessonCheckpoint,
    LessonNote,
    LessonPlan,
    LessonTranscript,
    LessonTranscriptEntry,
    StudentConceptMastery,
    StudentLearningObservation,
    StudentLearningProfile,
    User,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def clamp_page(limit: int | None, offset: int | None) -> tuple[int, int]:
    limit = DEFAULT_PAGE_SIZE if limit is None else int(limit)
    offset = 0 if offset is None else int(offset)
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    return limit, offset


# ---------------------------------------------------------------------------
# users / profile
# ---------------------------------------------------------------------------


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str,
    display_name: str,
    default_level: str = "high_school",
    default_duration_minutes: int = 20,
    preferences: dict[str, Any] | None = None,
) -> User:
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        display_name=display_name,
        default_level=default_level,
        default_duration_minutes=default_duration_minutes,
        preferences=preferences or {},
    )
    db.add(user)
    await db.flush()
    db.add(
        StudentLearningProfile(
            student_id=user.id,
            level=default_level,
            preferred_duration_minutes=default_duration_minutes,
            subjects_studied=[],
            strengths=[],
            weaknesses=[],
        )
    )
    await db.flush()
    return user


async def update_user(db: AsyncSession, user: User, **fields: Any) -> User:
    for key, value in fields.items():
        if value is not None and hasattr(User, key):
            setattr(user, key, value)
    user.updated_at = utcnow()
    db.add(user)
    await db.flush()
    return user


async def get_profile(db: AsyncSession, student_id: str) -> StudentLearningProfile | None:
    result = await db.execute(
        select(StudentLearningProfile).where(StudentLearningProfile.student_id == student_id)
    )
    return result.scalar_one_or_none()


async def upsert_profile(
    db: AsyncSession,
    student_id: str,
    *,
    level: str | None = None,
    preferred_duration_minutes: int | None = None,
    subjects_studied: Iterable[str] | None = None,
    strengths: Iterable[str] | None = None,
    weaknesses: Iterable[str] | None = None,
    notes: str | None = None,
) -> StudentLearningProfile:
    profile = await get_profile(db, student_id)
    if profile is None:
        profile = StudentLearningProfile(student_id=student_id)
        db.add(profile)
    if level is not None:
        profile.level = level
    if preferred_duration_minutes is not None:
        profile.preferred_duration_minutes = preferred_duration_minutes
    if subjects_studied is not None:
        profile.subjects_studied = list(subjects_studied)
    if strengths is not None:
        profile.strengths = list(strengths)
    if weaknesses is not None:
        profile.weaknesses = list(weaknesses)
    if notes is not None:
        profile.notes = notes
    profile.updated_at = utcnow()
    await db.flush()
    return profile


# ---------------------------------------------------------------------------
# lessons
# ---------------------------------------------------------------------------


async def create_lesson(
    db: AsyncSession,
    *,
    student_id: str,
    topic: str,
    subject: str | None,
    learning_level: str,
    duration_minutes: int,
    status: str = "created",
    session_metadata: dict[str, Any] | None = None,
) -> Lesson:
    lesson = Lesson(
        student_id=student_id,
        topic=topic,
        subject=subject,
        learning_level=learning_level,
        duration_minutes=duration_minutes,
        status=status,
        progress=0.0,
        session_metadata=session_metadata or {},
    )
    db.add(lesson)
    await db.flush()
    return lesson


async def get_lesson(db: AsyncSession, lesson_id: str) -> Lesson | None:
    return await db.get(Lesson, lesson_id)


async def list_lessons(
    db: AsyncSession,
    student_id: str,
    *,
    status: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> tuple[list[Lesson], int]:
    conditions = [Lesson.student_id == student_id]
    if status:
        conditions.append(Lesson.status == status)
    total = (
        await db.execute(select(func.count(Lesson.id)).where(*conditions))
    ).scalar_one()
    result = await db.execute(
        select(Lesson)
        .where(*conditions)
        .order_by(
            Lesson.last_activity_at.desc().nulls_last(),
            Lesson.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), int(total)


async def update_lesson(db: AsyncSession, lesson: Lesson, **fields: Any) -> Lesson:
    for key, value in fields.items():
        if hasattr(Lesson, key):
            setattr(lesson, key, value)
    lesson.updated_at = utcnow()
    db.add(lesson)
    await db.flush()
    return lesson


async def count_lessons(db: AsyncSession, student_id: str) -> int:
    return int(
        (await db.execute(select(func.count(Lesson.id)).where(Lesson.student_id == student_id))).scalar_one()
    )


# ---------------------------------------------------------------------------
# plans / checkpoints / assessments
# ---------------------------------------------------------------------------


async def get_lesson_plan(db: AsyncSession, lesson_id: str) -> LessonPlan | None:
    result = await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson_id))
    return result.scalar_one_or_none()


async def get_lesson_checkpoint(db: AsyncSession, lesson_id: str) -> LessonCheckpoint | None:
    result = await db.execute(
        select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson_id)
    )
    return result.scalar_one_or_none()


async def list_assessments(db: AsyncSession, lesson_id: str) -> list[LessonAssessment]:
    result = await db.execute(
        select(LessonAssessment)
        .where(LessonAssessment.lesson_id == lesson_id)
        .order_by(LessonAssessment.created_at.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------


async def get_transcript(db: AsyncSession, lesson_id: str) -> LessonTranscript | None:
    result = await db.execute(
        select(LessonTranscript).where(LessonTranscript.lesson_id == lesson_id)
    )
    return result.scalar_one_or_none()


async def transcript_entry_count(db: AsyncSession, lesson_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count(LessonTranscriptEntry.id)).where(
                    LessonTranscriptEntry.lesson_id == lesson_id
                )
            )
        ).scalar_one()
    )


async def lesson_has_transcript(db: AsyncSession, lesson_id: str) -> bool:
    return await transcript_entry_count(db, lesson_id) > 0


async def fetch_transcript_entries(
    db: AsyncSession,
    lesson_id: str,
    *,
    after_sequence: int = 0,
) -> list[LessonTranscriptEntry]:
    result = await db.execute(
        select(LessonTranscriptEntry)
        .where(
            LessonTranscriptEntry.lesson_id == lesson_id,
            LessonTranscriptEntry.sequence > after_sequence,
        )
        .order_by(LessonTranscriptEntry.sequence.asc())
    )
    return list(result.scalars().all())


async def transcript_presence_map(
    db: AsyncSession, lesson_ids: list[str]
) -> dict[str, int]:
    """lesson_id -> transcript entry count, in one grouped query."""
    if not lesson_ids:
        return {}
    result = await db.execute(
        select(LessonTranscriptEntry.lesson_id, func.count(LessonTranscriptEntry.id))
        .where(LessonTranscriptEntry.lesson_id.in_(lesson_ids))
        .group_by(LessonTranscriptEntry.lesson_id)
    )
    return {lesson_id: int(count) for lesson_id, count in result.all()}


async def notes_presence_map(db: AsyncSession, lesson_ids: list[str]) -> dict[str, int]:
    """lesson_id -> note count (pending/ready), in one grouped query."""
    if not lesson_ids:
        return {}
    result = await db.execute(
        select(LessonNote.lesson_id, func.count(LessonNote.id))
        .where(
            LessonNote.lesson_id.in_(lesson_ids),
            LessonNote.status.in_(("pending", "ready")),
        )
        .group_by(LessonNote.lesson_id)
    )
    return {lesson_id: int(count) for lesson_id, count in result.all()}


async def transcript_cursor(db: AsyncSession, lesson_id: str) -> int:
    transcript = await get_transcript(db, lesson_id)
    if transcript is not None:
        return int(transcript.last_sequence or 0)
    result = await db.execute(
        select(func.coalesce(func.max(LessonTranscriptEntry.sequence), 0)).where(
            LessonTranscriptEntry.lesson_id == lesson_id
        )
    )
    return int(result.scalar_one() or 0)


# ---------------------------------------------------------------------------
# board reads
# ---------------------------------------------------------------------------


async def latest_board_snapshot(
    db: AsyncSession, lesson_id: str
) -> LessonBoardSnapshot | None:
    result = await db.execute(
        select(LessonBoardSnapshot)
        .where(LessonBoardSnapshot.lesson_id == lesson_id)
        .order_by(LessonBoardSnapshot.revision.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def board_events_after(
    db: AsyncSession,
    lesson_id: str,
    *,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[LessonBoardEvent]:
    statement = (
        select(LessonBoardEvent)
        .where(
            LessonBoardEvent.lesson_id == lesson_id,
            LessonBoardEvent.sequence > after_sequence,
        )
        .order_by(LessonBoardEvent.sequence.asc())
    )
    if limit is not None:
        statement = statement.limit(max(1, int(limit)))
    result = await db.execute(statement)
    return list(result.scalars().all())


async def board_event_count(db: AsyncSession, lesson_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count(LessonBoardEvent.id)).where(
                    LessonBoardEvent.lesson_id == lesson_id
                )
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


async def create_note(
    db: AsyncSession,
    *,
    lesson: Lesson,
    title: str,
    source_transcript_id: str | None,
    status: str = "pending",
) -> LessonNote:
    note = LessonNote(
        lesson_id=lesson.id,
        student_id=lesson.student_id,
        title=title,
        subject=lesson.subject,
        status=status,
        content_markdown=None,
        sections=[],
        source_transcript_id=source_transcript_id,
        board_reference={},
        generated_by=None,
        error=None,
    )
    db.add(note)
    await db.flush()
    return note


async def get_note(db: AsyncSession, note_id: str) -> LessonNote | None:
    return await db.get(LessonNote, note_id)


async def get_note_for_student(db: AsyncSession, note_id: str, student_id: str) -> LessonNote | None:
    result = await db.execute(
        select(LessonNote).where(
            LessonNote.id == note_id, LessonNote.student_id == student_id
        )
    )
    return result.scalar_one_or_none()


async def pending_note_for_lesson(db: AsyncSession, lesson_id: str) -> LessonNote | None:
    result = await db.execute(
        select(LessonNote)
        .where(LessonNote.lesson_id == lesson_id, LessonNote.status == "pending")
        .order_by(LessonNote.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_notes_for_lesson(db: AsyncSession, lesson_id: str) -> list[LessonNote]:
    result = await db.execute(
        select(LessonNote)
        .where(LessonNote.lesson_id == lesson_id)
        .order_by(LessonNote.created_at.desc())
    )
    return list(result.scalars().all())


async def lesson_has_notes(db: AsyncSession, lesson_id: str) -> bool:
    result = await db.execute(
        select(func.count(LessonNote.id)).where(
            LessonNote.lesson_id == lesson_id,
            LessonNote.status.in_(("pending", "ready")),
        )
    )
    return int(result.scalar_one()) > 0


async def list_notes_for_student(
    db: AsyncSession,
    student_id: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> tuple[list[LessonNote], int]:
    conditions = [LessonNote.student_id == student_id]
    total = (
        await db.execute(select(func.count(LessonNote.id)).where(*conditions))
    ).scalar_one()
    result = await db.execute(
        select(LessonNote)
        .where(*conditions)
        .order_by(LessonNote.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), int(total)


# ---------------------------------------------------------------------------
# delete everything for one student (DELETE /me/data)
# ---------------------------------------------------------------------------


async def delete_student_data(db: AsyncSession, student_id: str) -> None:
    """Explicit cascade delete, ordered child -> parent.

    Mirrors the ORM ``ondelete=CASCADE`` graph but works even when a driver
    does not enforce foreign keys, and avoids relying on ORM relationship
    loading for a potentially large data set.
    """
    lesson_ids = select(Lesson.id).where(Lesson.student_id == student_id)

    await db.execute(delete(LessonNote).where(LessonNote.student_id == student_id))
    await db.execute(
        delete(LessonTranscriptEntry).where(LessonTranscriptEntry.lesson_id.in_(lesson_ids))
    )
    await db.execute(
        delete(LessonTranscript).where(LessonTranscript.lesson_id.in_(lesson_ids))
    )
    await db.execute(
        delete(LessonBoardEvent).where(LessonBoardEvent.lesson_id.in_(lesson_ids))
    )
    await db.execute(
        delete(LessonBoardSnapshot).where(LessonBoardSnapshot.lesson_id.in_(lesson_ids))
    )
    await db.execute(
        delete(LessonAssessment).where(LessonAssessment.lesson_id.in_(lesson_ids))
    )
    await db.execute(
        delete(LessonCheckpoint).where(LessonCheckpoint.lesson_id.in_(lesson_ids))
    )
    await db.execute(delete(LessonPlan).where(LessonPlan.lesson_id.in_(lesson_ids)))
    await db.execute(delete(Lesson).where(Lesson.student_id == student_id))
    await db.execute(
        delete(StudentConceptMastery).where(StudentConceptMastery.student_id == student_id)
    )
    await db.execute(
        delete(StudentLearningObservation).where(
            or_(
                StudentLearningObservation.student_id == student_id,
                StudentLearningObservation.lesson_id.in_(lesson_ids),
            )
        )
    )
    await db.execute(
        delete(StudentLearningProfile).where(StudentLearningProfile.student_id == student_id)
    )
    await db.execute(delete(User).where(User.id == student_id))
    await db.flush()
