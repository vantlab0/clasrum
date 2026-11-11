"""Module-level async API for the lesson graph (the REST/WS integration surface).

Consumed by ``backend-core`` exactly as documented:

* ``start_lesson(db, lesson) -> {"plan": ..., "state": ...}``
* ``load_lesson_state(lesson_id) -> dict | None``
* ``get_teaching_state(lesson_id) -> dict | None``
* ``finish_lesson(db, lesson_id, reason=None) -> dict``
* ``record_answer(...)``, ``advance_concept(...)``, ``delete_student_memory(...)``

plus the orchestration helpers used by the realtime tool registry
(:func:`run_lesson_tool`) and the FastAPI lifespan
(:func:`init_persistence` / :func:`shutdown_persistence`).

Persistence rules
-----------------
* The LangGraph checkpoint (``thread_id == lesson_id``) is authoritative; the
  functions here only ever *read* it back through ``graph.aget_state``.
* ``lesson_plans`` (full plan) and ``lesson_checkpoints`` (denormalised resume
  index) are written by the graph nodes on every run, so REST reads and crash
  recovery never depend on the checkpoint being reachable.
* A call that finds no checkpoint (fresh lesson, or in-memory fallback) seeds
  the graph from the resume index and then from the ``lessons`` row.
* All functions are idempotent enough to be invoked twice by a retrying client:
  planning reuses an existing plan, answer evidence dedupes, finishing keeps the
  first ``ended_at``.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.base import utcnow
from app.db.models import Lesson, LessonAssessment, LessonCheckpoint, LessonPlan
from app.db.session import session_scope
from app.graph.checkpointing import (
    configured_kind,
    init_persistence as _init_persistence,
    lesson_persistence,
    shutdown_persistence as _shutdown_persistence,
)
from app.graph.graph import (
    ACTION_ADVANCE,
    ACTION_ANSWER,
    ACTION_ASK_QUESTION,
    ACTION_FINISH,
    ACTION_LOAD,
    ACTION_REVISIT,
    ACTION_START,
    ACTION_UPDATE_PROGRESS,
    build_lesson_graph,
    graph_config,
)
from app.graph.memory import (
    delete_student_memory as _delete_student_memory_rows,
    store_delete_memory,
)
from app.graph.state import (
    LessonState,
    new_lesson_state,
    normalize_state,
    state_from_lesson,
    teaching_state,
)

logger = logging.getLogger(__name__)

#: classroom tool name -> graph action
LESSON_TOOL_ACTIONS: dict[str, str] = {
    "lesson_next_concept": ACTION_ADVANCE,
    "lesson_update_progress": ACTION_UPDATE_PROGRESS,
    "lesson_ask_question": ACTION_ASK_QUESTION,
    "lesson_check_answer": ACTION_ANSWER,
    "lesson_revisit_concept": ACTION_REVISIT,
    "lesson_finish": ACTION_FINISH,
}


# ---------------------------------------------------------------------------
# lifecycle hooks (FastAPI lifespan)
# ---------------------------------------------------------------------------


async def init_persistence() -> str:
    """Create/validate LangGraph checkpoint + Store tables. Idempotent, no args."""
    kind = await _init_persistence()
    logger.info("graph persistence ready (%s)", kind)
    return kind


async def shutdown_persistence() -> None:
    """Release LangGraph persistence resources (safe to call anytime)."""
    await _shutdown_persistence()


def persistence_kind() -> str:
    """The configured checkpoint backend: ``postgres``/``sqlite``/``memory``."""
    return configured_kind()


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


async def _load_resume_index(db: Any, lesson_id: str) -> LessonState | None:
    row = (
        await db.execute(select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if row is None or not isinstance(row.state, dict) or not row.state:
        return None
    return normalize_state(row.state)


async def _read_checkpoint_state(graph: Any, config: dict[str, Any]) -> LessonState | None:
    try:
        snapshot = await graph.aget_state(config)
    except Exception:  # pragma: no cover - backend-specific failures
        logger.debug("aget_state failed; falling back to resume index", exc_info=True)
        return None
    values = getattr(snapshot, "values", None)
    if not values:
        return None
    return normalize_state(values)


def _normalize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (payload or {}).items() if value is not None}


async def _run_action(
    db: Any,
    lesson_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> LessonState:
    """Run one graph action with durable checkpointing and return final state."""
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise ValueError(f"lesson {lesson_id} does not exist")

    async with lesson_persistence() as persistence:
        graph = build_lesson_graph(persistence.checkpointer, persistence.store)
        config = graph_config(
            lesson_id,
            db=db,
            store=persistence.store,
            action=action,
            payload=_normalize_payload(payload),
        )
        seed = await _read_checkpoint_state(graph, config)
        if seed is None:
            seed = await _load_resume_index(db, lesson_id)
        if seed is None:
            seed = state_from_lesson(lesson)
        if not seed.get("lesson_id"):
            seed["lesson_id"] = lesson_id
        if not seed.get("student_id"):
            seed["student_id"] = lesson.student_id
        # Keep request-level facts authoritative so a stale checkpoint can never
        # resurrect an old topic/level/duration for this lesson row.
        seed["topic"] = seed.get("topic") or lesson.topic
        seed["subject"] = seed.get("subject") or lesson.subject or "General Studies"
        seed["learning_level"] = seed.get("learning_level") or lesson.learning_level
        seed["duration_minutes"] = int(seed.get("duration_minutes") or lesson.duration_minutes or 20)

        final_values = await graph.ainvoke(seed, config)
    await db.flush()
    return normalize_state(final_values)


async def _run_action_and_commit(
    db: Any,
    lesson_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> LessonState:
    state = await _run_action(db, lesson_id, action, payload)
    await db.commit()
    return state


async def _latest_pending_question(db: Any, lesson_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(LessonAssessment)
            .where(
                LessonAssessment.lesson_id == lesson_id,
                LessonAssessment.correct.is_(None),
            )
            .order_by(LessonAssessment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "question_id": row.id,
        "question": row.question,
        "concept": row.concept,
        "expected_answer": row.expected_answer,
    }


def _state_payload(state: LessonState) -> dict[str, Any]:
    return {
        **normalize_state(state),
        "teaching_state": teaching_state(normalize_state(state)),
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


async def start_lesson(db: Any, lesson: Any) -> dict[str, Any]:
    """Create the plan + teaching state for a newly created lesson.

    Safe with no Qwen key and no prior persistence setup: the planner is
    deterministic and SQLite/in-memory checkpointing works out of the box.
    Persists ``lesson_plans`` and the ``lesson_checkpoints`` resume index itself
    (the caller must not insert a duplicate ``LessonPlan`` row).
    Returns ``{"plan": LessonPlan, "state": LessonState}``.
    """
    state = await _run_action_and_commit(db, lesson.id, ACTION_START)
    plan = dict(state.get("plan") or {})
    # Guarantee the REST DTO contract shape for the plan.
    plan.setdefault("objectives", [])
    plan.setdefault("concepts", [])
    for index, concept in enumerate(plan.get("concepts") or []):
        concept.setdefault("order", index + 1)
        concept.setdefault("objectives", [])
        concept.setdefault("status", "pending")
    return {"plan": plan, "state": _state_payload(state)}


async def load_lesson_state(lesson_id: str) -> dict[str, Any] | None:
    """Current teaching state for a lesson, or ``None`` when never started."""
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            return None
        try:
            async with lesson_persistence() as persistence:
                graph = build_lesson_graph(persistence.checkpointer, persistence.store)
                config = graph_config(
                    lesson_id,
                    db=db,
                    store=persistence.store,
                    action=ACTION_LOAD,
                )
                state = await _read_checkpoint_state(graph, config)
        except Exception:  # pragma: no cover - checkpoint backend failure
            logger.warning("checkpoint read failed lesson=%s", lesson_id, exc_info=True)
            state = None
        if state is None:
            state = await _load_resume_index(db, lesson_id)
        if state is None:
            return None
        return _state_payload(state)


async def get_teaching_state(lesson_id: str) -> dict[str, Any] | None:
    """TeachingState DTO (contract §5) for a lesson, or ``None``."""
    state = await load_lesson_state(lesson_id)
    if state is None:
        return None
    return state.get("teaching_state") or teaching_state(normalize_state(state))


async def load_lesson_plan(lesson_id: str) -> dict[str, Any] | None:
    """Lesson plan for REST DTOs (``lesson_plans`` table)."""
    async with session_scope() as db:
        row = (
            await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "objectives": list(row.objectives or []),
            "concepts": list(row.concepts or []),
        }


async def finish_lesson(db: Any, lesson_id: str, reason: str | None = None) -> dict[str, Any]:
    """Finalize a lesson: state completed, summary persisted, board snapshotted."""
    state = await _run_action_and_commit(
        db, lesson_id, ACTION_FINISH, {"reason": reason or "student_finished"}
    )
    logger.info(
        "lesson finished lesson=%s progress=%s", lesson_id, state.get("completed_concepts")
    )
    return _state_payload(state)


async def record_answer(
    db: Any,
    lesson_id: str,
    *,
    student_answer: str,
    correct: bool,
    question_id: str | None = None,
    concept: str | None = None,
    feedback: str | None = None,
    question: str | None = None,
    expected_answer: str | None = None,
) -> dict[str, Any]:
    """Record one evaluated answer and advance/flag the teaching state."""
    state = await _run_action_and_commit(
        db,
        lesson_id,
        ACTION_ANSWER,
        {
            "student_answer": student_answer,
            "correct": bool(correct),
            "question_id": question_id,
            "concept": concept,
            "feedback": feedback,
            "question": question,
            "expected_answer": expected_answer,
        },
    )
    return _state_payload(state)


async def advance_concept(
    db: Any,
    lesson_id: str,
    *,
    target_concept_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Advance to the next concept (or an explicit target)."""
    state = await _run_action_and_commit(
        db,
        lesson_id,
        ACTION_ADVANCE,
        {"target_concept_id": target_concept_id, "reason": reason},
    )
    return _state_payload(state)


async def revisit_concept(
    db: Any,
    lesson_id: str,
    *,
    concept_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Go back to an earlier concept and re-teach it."""
    state = await _run_action_and_commit(
        db, lesson_id, ACTION_REVISIT, {"concept_id": concept_id, "reason": reason}
    )
    return _state_payload(state)


async def ask_question(
    db: Any,
    lesson_id: str,
    *,
    question: str,
    concept: str | None = None,
    expected_answer: str | None = None,
) -> dict[str, Any]:
    """Record a question asked by the tutor and return the new teaching state."""
    state = await _run_action_and_commit(
        db,
        lesson_id,
        ACTION_ASK_QUESTION,
        {"question": question, "concept": concept, "expected_answer": expected_answer},
    )
    payload = _state_payload(state)
    pending = await _latest_pending_question(db, lesson_id)
    if pending is not None:
        payload["pending_question"] = pending
    return payload


async def update_progress(
    db: Any,
    lesson_id: str,
    *,
    progress: float | None = None,
    understanding_score: float | None = None,
    concept_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record observed progress / understanding for the current concept."""
    state = await _run_action_and_commit(
        db,
        lesson_id,
        ACTION_UPDATE_PROGRESS,
        {
            "progress": progress,
            "understanding_score": understanding_score,
            "concept_id": concept_id,
            "note": note,
        },
    )
    return _state_payload(state)


async def delete_student_memory(
    student_id: str, db: Any | None = None
) -> dict[str, Any]:
    """Erase a student's long-term memory (relational mirror + Store docs).

    Async, requires no prior persistence initialisation, and never raises when
    the Store backend is unconfigured.
    """
    if db is not None:
        counts = await _delete_student_memory_rows(db, student_id)
        await db.commit()
        store_counts = await _delete_store_memory(student_id)
        return {"deleted": True, "student_id": student_id, **counts, "store": store_counts}
    async with session_scope() as own_db:
        counts = await _delete_student_memory_rows(own_db, student_id)
        await own_db.commit()
    store_counts = await _delete_store_memory(student_id)
    return {"deleted": True, "student_id": student_id, **counts, "store": store_counts}


async def _delete_store_memory(student_id: str) -> dict[str, Any]:
    try:
        async with lesson_persistence() as persistence:
            if persistence.store is None:
                return {"deleted": False, "reason": "store_unavailable"}
            await store_delete_memory(persistence.store, student_id)
            return {"deleted": True, "kind": persistence.kind}
    except Exception:  # pragma: no cover - Store is optional
        logger.warning("store memory deletion failed student=%s", student_id, exc_info=True)
        return {"deleted": False, "reason": "store_error"}


# ---------------------------------------------------------------------------
# tool orchestration (used by app.board.registry)
# ---------------------------------------------------------------------------


async def run_lesson_tool(
    db: Any, lesson_id: str, tool_name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Execute one of the six lesson tools and return the TeachingState payload."""
    action = LESSON_TOOL_ACTIONS.get(tool_name)
    if action is None:
        raise ValueError(f"unknown lesson tool {tool_name!r}")

    if tool_name == "lesson_next_concept":
        state = await advance_concept(
            db,
            lesson_id,
            target_concept_id=args.get("target_concept_id"),
            reason=args.get("reason"),
        )
    elif tool_name == "lesson_update_progress":
        state = await update_progress(
            db,
            lesson_id,
            progress=args.get("progress"),
            understanding_score=args.get("understanding_score"),
            concept_id=args.get("concept_id"),
            note=args.get("note"),
        )
    elif tool_name == "lesson_ask_question":
        state = await ask_question(
            db,
            lesson_id,
            question=args.get("question") or "",
            concept=args.get("concept"),
            expected_answer=args.get("expected_answer"),
        )
    elif tool_name == "lesson_check_answer":
        state = await record_answer(
            db,
            lesson_id,
            student_answer=args.get("student_answer") or "",
            correct=bool(args.get("correct")),
            question_id=args.get("question_id"),
            concept=args.get("concept"),
            feedback=args.get("feedback"),
        )
    elif tool_name == "lesson_revisit_concept":
        state = await revisit_concept(
            db,
            lesson_id,
            concept_id=args.get("concept_id") or "",
            reason=args.get("reason"),
        )
    elif tool_name == "lesson_finish":
        state = await finish_lesson(db, lesson_id, reason=args.get("reason") or "tutor_finished")
    else:  # pragma: no cover - defensive
        raise ValueError(f"unhandled lesson tool {tool_name!r}")

    payload = _state_payload(state)
    # Keep the tool result compact: teaching state only (contract §4).
    return payload.get("teaching_state") or payload


async def resume_lesson(db: Any, lesson_id: str) -> dict[str, Any] | None:
    """Load (and mark live) the teaching state when a client resumes a lesson."""
    state = await load_lesson_state(lesson_id)
    if state is None:
        return None
    lesson = await db.get(Lesson, lesson_id)
    if lesson is not None and lesson.status == "paused":
        lesson.status = "live"
        lesson.last_activity_at = utcnow()
        await db.commit()
    return state


def new_state_for_lesson(lesson: Any) -> LessonState:
    """Public helper: an empty state for a lesson row (used by tests/tools)."""
    return state_from_lesson(lesson) if lesson is not None else new_lesson_state(
        lesson_id="", student_id="", topic="", subject="", learning_level="", duration_minutes=20
    )


__all__ = [
    "LESSON_TOOL_ACTIONS",
    "advance_concept",
    "ask_question",
    "delete_student_memory",
    "finish_lesson",
    "get_teaching_state",
    "init_persistence",
    "load_lesson_plan",
    "load_lesson_state",
    "new_state_for_lesson",
    "persistence_kind",
    "record_answer",
    "resume_lesson",
    "revisit_concept",
    "run_lesson_tool",
    "shutdown_persistence",
    "start_lesson",
    "update_progress",
]
