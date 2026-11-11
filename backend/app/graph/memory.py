"""Long-term student memory: retrieval, evidence-based mastery, observations.

Ownership (contract §8/§9): the LangGraph **Store** is the primary memory store
with namespaces ``("students", student_id, "profile" | "concepts" |
"observations")``; the relational tables ``student_concept_mastery``,
``student_learning_observations`` and ``student_learning_profiles`` are the
*queryable mirror* that the graph reads from on every turn (and that survives
in SQLite dev/test where the Store is in-memory). Both are always written
together by the functions here — one owner, two representations.

Mastery is evidence-based: it only changes from an evaluated answer, an explicit
revisit, or an explicit progress observation — never from a model monologue. It
is clamped to 0..5 and mapped to the status vocabulary
``not_started | learning | needs_practice | mastered``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import (
    Lesson,
    StudentConceptMastery,
    StudentLearningObservation,
    StudentLearningProfile,
    User,
)
from app.graph.state import (
    STATUS_MASTERED,
    STATUS_NEEDS_PRACTICE,
    STATUS_NOT_STARTED,
    StudentMemory,
    clamp_mastery,
    status_for_mastery,
)

logger = logging.getLogger(__name__)

MAX_MASTERY_ROWS = 20
MAX_OBSERVATION_ROWS = 10
MAX_RECENT_LESSONS = 5
MAX_OBSERVATION_CHARS = 600
MIN_MEANINGFUL_OBSERVATION_CHARS = 8
MAX_MISCONCEPTIONS = 5
MAX_STRENGTH_WEAKNESS = 8

VALID_OBSERVATION_KINDS = frozenset(
    {
        "observation",
        "answer_evaluated",
        "concept_completed",
        "concept_revisited",
        "difficulty",
        "strength",
        "question_asked",
        "lesson_summary",
    }
)

PROFILE_NAMESPACE = "profile"
CONCEPTS_NAMESPACE = "concepts"
OBSERVATIONS_NAMESPACE = "observations"


def student_namespace(student_id: str, name: str) -> tuple[str, str, str]:
    """Store namespace tuple for one memory kind (contract §9)."""
    return ("students", str(student_id), name)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def _recent(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


async def retrieve_memory(
    db: AsyncSession,
    student_id: str,
    *,
    subject: str | None = None,
    lesson_id: str | None = None,
    mastery_limit: int = MAX_MASTERY_ROWS,
    observation_limit: int = MAX_OBSERVATION_ROWS,
) -> StudentMemory:
    """Build the compact, prompt-safe memory snapshot for the teaching graph.

    Bounded on purpose: at most ``mastery_limit`` concepts, ``observation_limit``
    observations and short strings only. Never returns raw rows, emails, tokens
    or full transcripts.
    """
    memory = StudentMemory()

    # --- profile ---------------------------------------------------------
    profile_row = (
        await db.execute(
            select(StudentLearningProfile).where(StudentLearningProfile.student_id == student_id)
        )
    ).scalar_one_or_none()
    user = await db.get(User, student_id)
    profile: dict[str, Any] = {}
    if user is not None:
        profile["level"] = user.default_level
        profile["preferred_duration_minutes"] = user.default_duration_minutes
    if profile_row is not None:
        profile["level"] = profile_row.level or profile.get("level")
        profile["preferred_duration_minutes"] = (
            profile_row.preferred_duration_minutes or profile.get("preferred_duration_minutes")
        )
        profile["strengths"] = [str(item)[:80] for item in (profile_row.strengths or [])][:MAX_STRENGTH_WEAKNESS]
        profile["weaknesses"] = [str(item)[:80] for item in (profile_row.weaknesses or [])][:MAX_STRENGTH_WEAKNESS]
        profile["subjects_studied"] = [
            str(item)[:60] for item in (profile_row.subjects_studied or [])
        ][:MAX_STRENGTH_WEAKNESS]
    memory["profile"] = profile
    memory["subjects_studied"] = list(profile.get("subjects_studied") or [])

    # --- concept mastery -------------------------------------------------
    mastery_stmt = select(StudentConceptMastery).where(
        StudentConceptMastery.student_id == student_id
    )
    if subject:
        mastery_stmt = mastery_stmt.where(StudentConceptMastery.subject == subject)
    mastery_stmt = mastery_stmt.order_by(
        StudentConceptMastery.last_reviewed_at.desc().nullslast(),
        StudentConceptMastery.updated_at.desc(),
    ).limit(max(1, min(int(mastery_limit), 100)))
    mastery_rows = (await db.execute(mastery_stmt)).scalars().all()
    mastery: list[dict[str, Any]] = []
    for row in mastery_rows:
        mastery.append(
            {
                "concept": str(row.concept)[:120],
                "subject": str(row.subject)[:60],
                "mastery_level": clamp_mastery(row.mastery_level),
                "status": row.status if row.status in {
                    STATUS_NOT_STARTED,
                    "learning",
                    STATUS_NEEDS_PRACTICE,
                    STATUS_MASTERED,
                } else status_for_mastery(row.mastery_level),
                "attempts": int(row.attempts or 0),
                "correct_answers": int(row.correct_answers or 0),
                "misconceptions": [str(item)[:120] for item in (row.misconceptions or [])][:MAX_MISCONCEPTIONS],
            }
        )
    memory["mastery"] = mastery

    # --- prior difficulty (explicit hint for the planner/teacher) ---------
    difficulty: list[str] = []
    for item in mastery:
        if item["status"] in {STATUS_NEEDS_PRACTICE, STATUS_NOT_STARTED} and item["attempts"] > 0:
            difficulty.append(item["concept"])
    memory["prior_difficulty"] = difficulty[:5]

    # --- observations ----------------------------------------------------
    obs_stmt = (
        select(StudentLearningObservation)
        .where(StudentLearningObservation.student_id == student_id)
        .order_by(StudentLearningObservation.created_at.desc())
        .limit(max(1, min(int(observation_limit), 50)))
    )
    observation_rows = (await db.execute(obs_stmt)).scalars().all()
    observations: list[dict[str, Any]] = []
    for row in observation_rows:
        text = str(row.observation or "").strip()
        if not text:
            continue
        observations.append(
            {
                "concept": str(row.concept or "")[:120],
                "kind": str(row.kind or "observation")[:48],
                "observation": text[:MAX_OBSERVATION_CHARS],
                "created_at": _recent(row.created_at),
            }
        )
    memory["observations"] = observations

    # --- lesson history (bounded, no transcripts) ------------------------
    history_stmt = (
        select(Lesson)
        .where(Lesson.student_id == student_id, Lesson.status == "completed")
        .order_by(Lesson.ended_at.desc().nullslast())
        .limit(MAX_RECENT_LESSONS)
    )
    history_rows = (await db.execute(history_stmt)).scalars().all()
    memory["lesson_history"] = [
        {
            "topic": str(row.topic)[:160],
            "subject": str(row.subject or "")[:60],
            "progress": round(float(row.progress or 0.0), 3),
            "ended_at": _recent(row.ended_at),
        }
        for row in history_rows
    ]
    return memory


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------


async def record_observation(
    db: AsyncSession,
    *,
    student_id: str,
    lesson_id: str | None,
    subject: str,
    concept: str,
    observation: str,
    kind: str = "observation",
) -> dict[str, Any] | None:
    """Record a *meaningful* learning observation (returns ``None`` when trivial).

    Trivial/duplicate observations are dropped so the memory stays useful: the
    text must be non-empty, at least :data:`MIN_MEANINGFUL_OBSERVATION_CHARS`
    characters, and not a repeat of the latest observation for the same concept.
    """
    text = str(observation or "").strip()
    if len(text) < MIN_MEANINGFUL_OBSERVATION_CHARS:
        return None
    if kind not in VALID_OBSERVATION_KINDS:
        kind = "observation"
    text = text[:MAX_OBSERVATION_CHARS]
    concept = str(concept or "")[:240]
    subject = str(subject or "")[:120]

    latest = (
        await db.execute(
            select(StudentLearningObservation)
            .where(
                StudentLearningObservation.student_id == student_id,
                StudentLearningObservation.concept == concept,
            )
            .order_by(StudentLearningObservation.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None and str(latest.observation or "").strip() == text:
        return None

    row = StudentLearningObservation(
        student_id=student_id,
        lesson_id=lesson_id,
        subject=subject,
        concept=concept,
        kind=kind,
        observation=text,
    )
    db.add(row)
    await db.flush()
    return {
        "id": row.id,
        "concept": concept,
        "kind": kind,
        "observation": text,
        "created_at": _recent(row.created_at or utcnow()),
    }


# ---------------------------------------------------------------------------
# mastery
# ---------------------------------------------------------------------------


def _delta_for_evidence(correct: bool | None, delta: int | None) -> int:
    if delta is not None:
        try:
            return max(-3, min(3, int(delta)))
        except (TypeError, ValueError):
            return 0
    if correct is True:
        return 1
    if correct is False:
        return -1
    return 0


async def update_mastery(
    db: AsyncSession,
    *,
    student_id: str,
    subject: str,
    concept: str,
    correct: bool | None = None,
    delta: int | None = None,
    misconception: str | None = None,
) -> dict[str, Any]:
    """Update one concept's mastery from evidence, clamped to 0..5."""
    subject = str(subject or "").strip()[:120] or "General Studies"
    concept = str(concept or "").strip()[:240]
    if not concept:
        return {
            "concept": concept,
            "mastery_level": 0,
            "status": STATUS_NOT_STARTED,
            "mastery_delta": 0,
        }
    row = (
        await db.execute(
            select(StudentConceptMastery).where(
                StudentConceptMastery.student_id == student_id,
                StudentConceptMastery.subject == subject,
                StudentConceptMastery.concept == concept,
            )
        )
    ).scalar_one_or_none()
    created = row is None
    if row is None:
        row = StudentConceptMastery(
            student_id=student_id,
            subject=subject,
            concept=concept,
            mastery_level=0,
            status=STATUS_NOT_STARTED,
            attempts=0,
            correct_answers=0,
            misconceptions=[],
        )
        db.add(row)
        await db.flush()

    mastery_delta = _delta_for_evidence(correct, delta)
    previous = clamp_mastery(row.mastery_level or 0)
    row.mastery_level = clamp_mastery(previous + mastery_delta)
    if correct is not None:
        row.attempts = int(row.attempts or 0) + 1
        if correct:
            row.correct_answers = int(row.correct_answers or 0) + 1
    row.status = status_for_mastery(row.mastery_level)
    mistakes = list(row.misconceptions or [])
    if misconception:
        cleaned = str(misconception).strip()[:240]
        if cleaned and cleaned not in mistakes:
            mistakes.append(cleaned)
            row.misconceptions = mistakes[-MAX_MISCONCEPTIONS:]
    row.last_reviewed_at = utcnow()
    await db.flush()

    return {
        "concept": row.concept,
        "subject": row.subject,
        "mastery_level": clamp_mastery(row.mastery_level),
        "previous_level": previous,
        "mastery_delta": mastery_delta,
        "status": row.status,
        "attempts": int(row.attempts or 0),
        "correct_answers": int(row.correct_answers or 0),
        "created": created,
    }


# ---------------------------------------------------------------------------
# deletion (student data erasure, contract §5 DELETE /me/data)
# ---------------------------------------------------------------------------


async def delete_student_memory(db: AsyncSession, student_id: str) -> dict[str, Any]:
    """Delete all relational memory rows for a student (idempotent)."""
    observations = await db.execute(
        delete(StudentLearningObservation).where(
            StudentLearningObservation.student_id == student_id
        )
    )
    mastery = await db.execute(
        delete(StudentConceptMastery).where(StudentConceptMastery.student_id == student_id)
    )
    profile = await db.execute(
        delete(StudentLearningProfile).where(StudentLearningProfile.student_id == student_id)
    )
    await db.flush()
    return {
        "student_id": student_id,
        "observations_deleted": int(getattr(observations, "rowcount", 0) or 0),
        "mastery_deleted": int(getattr(mastery, "rowcount", 0) or 0),
        "profile_deleted": int(getattr(profile, "rowcount", 0) or 0),
    }


# ---------------------------------------------------------------------------
# LangGraph Store mirror (best-effort; relational tables remain queryable)
# ---------------------------------------------------------------------------


async def store_put_memory(
    store: Any,
    *,
    student_id: str,
    profile: dict[str, Any] | None = None,
    mastery: dict[str, Any] | None = None,
    observation: dict[str, Any] | None = None,
) -> None:
    """Mirror memory documents into the LangGraph Store (no-op if unavailable)."""
    if store is None:
        return
    try:
        if profile is not None:
            await store.aput(student_namespace(student_id, PROFILE_NAMESPACE), "profile", profile)
        if mastery is not None and mastery.get("concept"):
            key = str(mastery["concept"])[:100]
            await store.aput(
                student_namespace(student_id, CONCEPTS_NAMESPACE),
                key,
                dict(mastery),
            )
        if observation is not None and observation.get("observation"):
            key = str(observation.get("id") or uuid.uuid4().hex)[:100]
            await store.aput(
                student_namespace(student_id, OBSERVATIONS_NAMESPACE),
                key,
                dict(observation),
            )
    except Exception:  # pragma: no cover - memory mirror must never break a lesson
        logger.warning("LangGraph Store memory write failed student=%s", student_id, exc_info=True)


async def store_retrieve_memory(store: Any, student_id: str) -> dict[str, Any]:
    """Read the Store mirror (used to merge into retrieval when available)."""
    result: dict[str, Any] = {}
    if store is None:
        return result
    try:
        profile_items = await store.asearch(student_namespace(student_id, PROFILE_NAMESPACE), limit=1)
        if profile_items:
            result["profile"] = dict(profile_items[0].value)
        concept_items = await store.asearch(student_namespace(student_id, CONCEPTS_NAMESPACE), limit=MAX_MASTERY_ROWS)
        if concept_items:
            result["mastery"] = [dict(item.value) for item in concept_items]
        observation_items = await store.asearch(
            student_namespace(student_id, OBSERVATIONS_NAMESPACE), limit=MAX_OBSERVATION_ROWS
        )
        if observation_items:
            result["observations"] = [dict(item.value) for item in observation_items]
    except Exception:  # pragma: no cover - Store is optional
        logger.debug("LangGraph Store memory read failed student=%s", student_id, exc_info=True)
    return result


async def store_delete_memory(store: Any, student_id: str) -> None:
    """Best-effort deletion of all Store memory for a student."""
    if store is None:
        return
    try:
        for namespace in (PROFILE_NAMESPACE, CONCEPTS_NAMESPACE, OBSERVATIONS_NAMESPACE):
            items = await store.asearch(student_namespace(student_id, namespace), limit=100)
            for item in items:
                await store.adelete(student_namespace(student_id, namespace), item.key)
    except Exception:  # pragma: no cover - Store is optional
        logger.warning("LangGraph Store memory delete failed student=%s", student_id, exc_info=True)


__all__ = [
    "CONCEPTS_NAMESPACE",
    "MAX_MASTERY_ROWS",
    "MAX_OBSERVATION_ROWS",
    "OBSERVATIONS_NAMESPACE",
    "PROFILE_NAMESPACE",
    "VALID_OBSERVATION_KINDS",
    "delete_student_memory",
    "record_observation",
    "retrieve_memory",
    "status_for_mastery",
    "store_delete_memory",
    "store_put_memory",
    "store_retrieve_memory",
    "student_namespace",
    "update_mastery",
]
