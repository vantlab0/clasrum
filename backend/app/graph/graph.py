"""The LangGraph lesson state machine.

Contract: ``docs/CONTRACTS.md`` §9. ``thread_id == lesson_id``; the LangGraph
checkpoint is authoritative, ``lesson_checkpoints`` is a resume index.

Graph:

    retrieve_memory
        ├─(plan missing)──> plan_lesson ─┐
        └─(plan present)──> load_plan ───┤
                                         v
                                     teach_step
              ┌────────(action=answer)────┤
              │                          ├──(action=advance|revisit)──> evaluate_and_advance
              v                          ├──(action=finish)───────────> finalize_lesson
          record_answer ──> evaluate_and_advance ──> persist_observations ──> END
                                         └──(start/ask_question/update_progress)──> persist_observations ──> END

Every node is idempotent enough that the whole action can be re-run after a
crash/duplicate request: planning reuses an existing plan, answer evidence is
deduplicated by ``(question_id, student_answer)``, observations deduplicate by
text, and finalisation keeps the first ``ended_at``.
"""
from __future__ import annotations

import copy
import logging
import math
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, StateGraph

from app.db.constants import ConceptStatus, LessonStatus
from app.db.models import Lesson, LessonAssessment, LessonCheckpoint, LessonPlan
from app.graph import memory as memory_service
from app.graph import planner
from app.graph.state import (
    AssessmentResult,
    LessonState,
    clamp,
    compute_progress,
    concept_by_id,
    concept_by_title_or_id,
    normalize_state,
    now_iso,
    teaching_state,
)

logger = logging.getLogger(__name__)

MAX_ANSWER_EVIDENCE = 200

#: Action values accepted by the graph (passed via ``configurable.action``).
ACTION_START = "start"
ACTION_LOAD = "load"
ACTION_ANSWER = "answer"
ACTION_ADVANCE = "advance"
ACTION_REVISIT = "revisit"
ACTION_ASK_QUESTION = "ask_question"
ACTION_UPDATE_PROGRESS = "update_progress"
ACTION_FINISH = "finish"
ACTION_TEACH = "teach"

VALID_ACTIONS = frozenset(
    {
        ACTION_START,
        ACTION_LOAD,
        ACTION_ANSWER,
        ACTION_ADVANCE,
        ACTION_REVISIT,
        ACTION_ASK_QUESTION,
        ACTION_UPDATE_PROGRESS,
        ACTION_FINISH,
        ACTION_TEACH,
    }
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _configurable(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return dict(config.get("configurable") or {})
    return {}


def _db(config: Any) -> Any:
    db = _configurable(config).get("db")
    if db is None:  # pragma: no cover - service always supplies a session
        raise RuntimeError("graph invoked without a database session in config['configurable']['db']")
    return db


def _store(config: Any) -> Any:
    return _configurable(config).get("store")


def _action(config: Any) -> str:
    action = str(_configurable(config).get("action") or ACTION_TEACH)
    return action if action in VALID_ACTIONS else ACTION_TEACH


def _payload(config: Any) -> dict[str, Any]:
    payload = _configurable(config).get("action_payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_seconds(lesson: Lesson, state: LessonState) -> int:
    if lesson.started_at is not None:
        started = lesson.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        delta = _utcnow() - started
        return max(0, int(delta.total_seconds()))
    return max(0, int(state.get("elapsed_seconds") or 0))


def _timing(lesson: Lesson, state: LessonState) -> tuple[int, int]:
    elapsed = _elapsed_seconds(lesson, state)
    total_seconds = max(60, int(state.get("duration_minutes") or lesson.duration_minutes or 20) * 60)
    return elapsed, max(0, total_seconds - elapsed)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _concept_title(plan: Any, concept_id: str | None) -> str:
    concept = concept_by_id(plan or {}, concept_id)
    return str((concept or {}).get("title") or concept_id or "")


def _set_concept_status(plan: dict[str, Any], concept_id: str, status: str) -> None:
    concept = concept_by_id(plan, concept_id)
    if concept is not None:
        concept["status"] = status


def _next_concept_id(plan: dict[str, Any], completed: set[str], after_order: int = 0) -> str | None:
    candidates = [
        concept
        for concept in (plan or {}).get("concepts", [])
        if concept.get("id") and concept["id"] not in completed
    ]
    candidates.sort(key=lambda concept: int(concept.get("order") or 0))
    for concept in candidates:
        if int(concept.get("order") or 0) > after_order:
            return str(concept["id"])
    return str(candidates[0]["id"]) if candidates else None


def _ensure_started(db: Any, lesson: Lesson, state: LessonState) -> None:
    if lesson.started_at is None:
        lesson.started_at = _utcnow()
    if lesson.status in {LessonStatus.CREATED, LessonStatus.PAUSED, None}:
        lesson.status = LessonStatus.LIVE
    lesson.last_activity_at = _utcnow()


async def _upsert_plan_row(db: Any, lesson_id: str, plan: dict[str, Any]) -> None:
    from sqlalchemy import select

    row = (
        await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson_id))
    ).scalar_one_or_none()
    objectives = list(plan.get("objectives") or [])
    concepts = list(plan.get("concepts") or [])
    if row is None:
        db.add(
            LessonPlan(
                lesson_id=lesson_id,
                objectives=objectives,
                concepts=concepts,
                is_final=False,
            )
        )
    else:
        row.objectives = objectives
        row.concepts = concepts
        row.updated_at = _utcnow()
    await db.flush()


async def _upsert_checkpoint_index(db: Any, lesson_id: str, state: LessonState) -> None:
    from sqlalchemy import select

    row = (
        await db.execute(select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson_id))
    ).scalar_one_or_none()
    payload = _json_safe(dict(state))
    if row is None:
        db.add(
            LessonCheckpoint(
                lesson_id=lesson_id,
                langgraph_thread_id=lesson_id,
                state=payload,
            )
        )
    else:
        row.langgraph_thread_id = lesson_id
        row.state = payload
        row.updated_at = _utcnow()
    await db.flush()


async def _load_plan_from_row(db: Any, lesson_id: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    row = (
        await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if row is None:
        return None
    plan = {
        "objectives": list(row.objectives or []),
        "concepts": list(row.concepts or []),
        "generated_by": "deterministic",
    }
    return plan if planner.plan_is_usable(plan) else None


async def _sync_lesson_row(
    db: Any,
    lesson: Lesson,
    state: LessonState,
    *,
    elapsed: int,
    remaining: int,
    finalize: bool = False,
) -> None:
    from sqlalchemy import select

    progress = compute_progress(state)
    lesson.progress = round(progress, 4)
    concept_title = _concept_title(state.get("plan") or {}, state.get("current_concept"))
    if concept_title:
        lesson.current_concept = concept_title[:240]
    lesson.elapsed_seconds = int(elapsed)
    lesson.last_activity_at = _utcnow()
    board_revision = (
        await db.execute(select(Lesson.board_revision).where(Lesson.id == lesson.id))
    ).scalar_one_or_none()
    lesson.board_revision = int(board_revision or 0)
    if finalize:
        lesson.status = LessonStatus.COMPLETED
        if lesson.ended_at is None:
            lesson.ended_at = _utcnow()
        lesson.progress = max(round(progress, 4), 0.0)
    elif lesson.status in {LessonStatus.CREATED, LessonStatus.PAUSED, None}:
        lesson.status = LessonStatus.LIVE


async def _record_evidence(
    db: Any,
    *,
    lesson: Lesson,
    state: LessonState,
    action: str,
    payload: dict[str, Any],
    store: Any,
) -> dict[str, Any] | None:
    """Persist a *meaningful* observation + evidence-based mastery update.

    Returns the observation row dict when one was written (used as the
    idempotency gate for mastery updates).
    """
    student_id = state.get("student_id") or lesson.student_id
    subject = state.get("subject") or lesson.subject or "General Studies"
    plan = state.get("plan") or {}
    observation: dict[str, Any] | None = None
    mastery_update: dict[str, Any] | None = None

    if action == ACTION_ANSWER:
        results = state.get("assessment_results") or []
        latest = results[-1] if results else None
        if isinstance(latest, dict):
            concept_title = _concept_title(plan, state.get("current_concept")) or str(
                latest.get("concept") or ""
            )
            correct = bool(latest.get("correct"))
            verdict = "correct" if correct else "incorrect"
            text = f"Answered a question on {concept_title or 'the concept'}: {verdict}."
            feedback = str(latest.get("feedback") or "").strip()
            if feedback:
                text += f" Feedback: {feedback[:200]}"
            observation = await memory_service.record_observation(
                db,
                student_id=student_id,
                lesson_id=lesson.id,
                subject=subject,
                concept=concept_title or str(latest.get("concept") or ""),
                observation=text,
                kind="answer_evaluated",
            )
            if observation is not None and concept_title:
                mastery_update = await memory_service.update_mastery(
                    db,
                    student_id=student_id,
                    subject=subject,
                    concept=concept_title,
                    correct=correct,
                    misconception=None if correct else (feedback or "incorrect answer"),
                )

    elif action == ACTION_ADVANCE:
        completed = state.get("completed_concepts") or []
        concept_title = _concept_title(plan, completed[-1] if completed else state.get("current_concept"))
        if concept_title:
            observation = await memory_service.record_observation(
                db,
                student_id=student_id,
                lesson_id=lesson.id,
                subject=subject,
                concept=concept_title,
                observation=f"Completed the concept '{concept_title}' and moved on.",
                kind="concept_completed",
            )
            if observation is not None:
                mastery_update = await memory_service.update_mastery(
                    db,
                    student_id=student_id,
                    subject=subject,
                    concept=concept_title,
                    correct=None,
                    delta=1,
                )

    elif action == ACTION_REVISIT:
        needle = str(payload.get("concept_id") or "")
        concept_title = _concept_title(plan, state.get("current_concept")) or needle
        if concept_title:
            reason = str(payload.get("reason") or "").strip()
            text = f"Revisited the concept '{concept_title}'"
            text += f" because {reason[:160]}" if reason else " to re-teach it."
            observation = await memory_service.record_observation(
                db,
                student_id=student_id,
                lesson_id=lesson.id,
                subject=subject,
                concept=concept_title,
                observation=text,
                kind="concept_revisited",
            )
            if observation is not None:
                mastery_update = await memory_service.update_mastery(
                    db,
                    student_id=student_id,
                    subject=subject,
                    concept=concept_title,
                    correct=None,
                    delta=-1,
                )

    elif action == ACTION_UPDATE_PROGRESS:
        note = str(payload.get("note") or "").strip()
        concept_title = _concept_title(plan, state.get("current_concept"))
        if note and concept_title:
            observation = await memory_service.record_observation(
                db,
                student_id=student_id,
                lesson_id=lesson.id,
                subject=subject,
                concept=concept_title,
                observation=note,
                kind="observation",
            )

    if observation is not None and store is not None:
        await memory_service.store_put_memory(
            store,
            student_id=student_id,
            mastery=mastery_update,
            observation=observation,
        )
    return observation


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------


async def retrieve_memory_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    lesson_id = state.get("lesson_id")
    student_id = state.get("student_id")
    if not lesson_id or not student_id:
        lesson = await db.get(Lesson, lesson_id or "")
        if lesson is None:
            raise ValueError("lesson state is missing lesson_id/student_id")
        state = {**state, "lesson_id": lesson.id, "student_id": lesson.student_id}
        lesson_id, student_id = lesson.id, lesson.student_id
    memory = await memory_service.retrieve_memory(
        db, student_id, subject=state.get("subject") or None, lesson_id=lesson_id
    )
    store = _store(config)
    if store is not None:
        store_memory = await memory_service.store_retrieve_memory(store, student_id)
        if not memory.get("mastery") and store_memory.get("mastery"):
            memory["mastery"] = store_memory["mastery"]
        if not memory.get("observations") and store_memory.get("observations"):
            memory["observations"] = store_memory["observations"]
        if not memory.get("profile") and store_memory.get("profile"):
            memory["profile"] = store_memory["profile"]
    return {"student_memory": memory, "last_updated": now_iso()}


async def plan_lesson_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")
    plan = planner.generate_plan(
        topic=state.get("topic") or lesson.topic,
        subject=state.get("subject") or lesson.subject,
        learning_level=state.get("learning_level") or lesson.learning_level,
        duration_minutes=state.get("duration_minutes") or lesson.duration_minutes,
        student_memory=state.get("student_memory") or {},
    )
    await _upsert_plan_row(db, lesson.id, dict(plan))
    _ensure_started(db, lesson, state)
    first = planner.first_actionable_concept(plan)
    concept_ids = [str(concept["id"]) for concept in plan.get("concepts", [])]
    current = str(first["id"]) if first is not None else None
    if current:
        _set_concept_status(plan, current, ConceptStatus.IN_PROGRESS)
        await _upsert_plan_row(db, lesson.id, dict(plan))
    total_seconds = max(60, int(state.get("duration_minutes") or lesson.duration_minutes or 20) * 60)
    return {
        "plan": plan,
        "subject": plan.get("subject") or state.get("subject") or lesson.subject or "General Studies",
        "current_concept": current,
        "completed_concepts": [],
        "pending_concepts": concept_ids,
        "status": LessonStatus.LIVE,
        "recommended_action": "teach_concept",
        "elapsed_seconds": 0,
        "remaining_seconds": total_seconds,
        "last_updated": now_iso(),
    }


async def load_plan_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")
    plan = dict(state.get("plan") or {})
    if not planner.plan_is_usable(plan):
        stored = await _load_plan_from_row(db, lesson.id)
        plan = stored or dict(
            planner.generate_plan(
                topic=state.get("topic") or lesson.topic,
                subject=state.get("subject") or lesson.subject,
                learning_level=state.get("learning_level") or lesson.learning_level,
                duration_minutes=state.get("duration_minutes") or lesson.duration_minutes,
                student_memory=state.get("student_memory") or {},
            )
        )
        await _upsert_plan_row(db, lesson.id, plan)

    concepts = list(plan.get("concepts") or [])
    concept_ids = [str(concept["id"]) for concept in concepts]
    completed = [item for item in (state.get("completed_concepts") or []) if item in set(concept_ids)]
    if not completed:
        completed = [
            str(concept["id"])
            for concept in concepts
            if concept.get("status") == ConceptStatus.COMPLETED
        ]
    pending = [item for item in (state.get("pending_concepts") or []) if item in set(concept_ids)]
    if not pending:
        pending = [item for item in concept_ids if item not in set(completed)]
    current = state.get("current_concept")
    if current not in set(concept_ids):
        current = planner.first_actionable_concept(plan)
        current = str(current["id"]) if current else None
    if current and current not in set(completed):
        _set_concept_status(plan, current, ConceptStatus.IN_PROGRESS)
    _ensure_started(db, lesson, state)
    elapsed, remaining = _timing(lesson, state)
    return {
        "plan": plan,
        "subject": plan.get("subject") or state.get("subject") or lesson.subject or "General Studies",
        "current_concept": current,
        "completed_concepts": completed,
        "pending_concepts": pending,
        "status": state.get("status")
        if state.get("status") in {LessonStatus.LIVE, LessonStatus.PAUSED, LessonStatus.COMPLETED}
        else LessonStatus.LIVE,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "last_updated": now_iso(),
    }


async def teach_step_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    action = _action(config)
    payload = _payload(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")
    _ensure_started(db, lesson, state)
    plan = copy.deepcopy(dict(state.get("plan") or {}))
    current = state.get("current_concept")
    recommended = state.get("recommended_action") or "teach_concept"

    if action == ACTION_ASK_QUESTION:
        concept = concept_by_id(plan, current)
        concept_title = str((concept or {}).get("title") or payload.get("concept") or "")
        question = str(payload.get("question") or "").strip()
        if question:
            db.add(
                LessonAssessment(
                    lesson_id=lesson.id,
                    concept=concept_title[:240],
                    question=question[:4000],
                    expected_answer=(str(payload.get("expected_answer"))[:4000]
                    if payload.get("expected_answer")
                    else None),
                    correct=None,
                )
            )
            await db.flush()
        recommended = "await_answer"

    elif action == ACTION_ADVANCE:
        target = concept_by_title_or_id(plan, payload.get("target_concept_id"))
        if target is not None and target.get("id") != current:
            if target.get("status") != ConceptStatus.COMPLETED:
                _set_concept_status(plan, str(target["id"]), ConceptStatus.IN_PROGRESS)
            current = str(target["id"])
        recommended = "teach_concept"

    elif action == ACTION_REVISIT:
        target = concept_by_title_or_id(plan, payload.get("concept_id"))
        if target is not None:
            _set_concept_status(plan, str(target["id"]), ConceptStatus.IN_PROGRESS)
            current = str(target["id"])
        recommended = "reteach_concept"

    elif action == ACTION_UPDATE_PROGRESS:
        if payload.get("concept_id"):
            target = concept_by_title_or_id(plan, payload.get("concept_id"))
            if target is not None:
                current = str(target["id"])
        recommended = state.get("recommended_action") or "teach_concept"

    elif action == ACTION_START:
        if current:
            _set_concept_status(plan, current, ConceptStatus.IN_PROGRESS)
        recommended = "teach_concept"

    elapsed, remaining = _timing(lesson, state)
    return {
        "plan": plan,
        "current_concept": current,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "status": LessonStatus.LIVE if state.get("status") != LessonStatus.COMPLETED else LessonStatus.COMPLETED,
        "recommended_action": recommended,
        "last_updated": now_iso(),
    }


async def record_answer_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    payload = _payload(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")
    results = list(state.get("assessment_results") or [])
    student_answer = str(payload.get("student_answer") or "").strip()
    if not student_answer:
        return {"last_updated": now_iso()}
    correct = bool(payload.get("correct"))
    question_id = payload.get("question_id")
    question_id = str(question_id) if question_id else None
    feedback = str(payload.get("feedback") or "").strip() or None

    # Idempotency: re-running the same answer evidence is a no-op.
    for existing in results[-5:]:
        if (
            str(existing.get("question_id") or "") == str(question_id or "")
            and str(existing.get("student_answer") or "") == student_answer
        ):
            return {"last_updated": now_iso()}

    plan = state.get("plan") or {}
    concept_id = state.get("current_concept")
    concept_title = _concept_title(plan, concept_id) or str(payload.get("concept") or "")

    assessment_row: LessonAssessment | None = None
    if question_id:
        assessment_row = await db.get(LessonAssessment, question_id)
    if assessment_row is None and question_id:
        from sqlalchemy import select

        assessment_row = (
            await db.execute(select(LessonAssessment).where(LessonAssessment.id == question_id))
        ).scalar_one_or_none()
    if assessment_row is not None:
        assessment_row.student_answer = student_answer[:4000]
        assessment_row.correct = correct
        assessment_row.feedback = feedback
        if concept_title:
            assessment_row.concept = concept_title[:240]
    else:
        assessment_row = LessonAssessment(
            lesson_id=lesson.id,
            concept=concept_title[:240],
            question=str(payload.get("question") or "Student response")[:4000],
            expected_answer=(str(payload.get("expected_answer"))[:4000] if payload.get("expected_answer") else None),
            student_answer=student_answer[:4000],
            correct=correct,
            feedback=feedback,
        )
        db.add(assessment_row)
        await db.flush()

    result: AssessmentResult = AssessmentResult(
        question_id=assessment_row.id,
        concept=concept_title,
        question=str(assessment_row.question or "")[:4000],
        expected_answer=assessment_row.expected_answer,
        student_answer=student_answer[:2000],
        correct=correct,
        feedback=feedback,
        mastery_delta=2 if correct else -2,
        at=now_iso(),
    )
    results.append(result)
    results = results[-MAX_ANSWER_EVIDENCE:]
    correct_count = sum(1 for item in results if item.get("correct"))
    understanding = round(clamp(correct_count / len(results)), 4) if results else 0.0
    return {
        "assessment_results": results,
        "understanding_score": understanding,
        "recommended_action": "advance_concept" if correct else "reteach_concept",
        "last_updated": now_iso(),
    }


async def evaluate_and_advance_node(state: LessonState, config: Any) -> dict[str, Any]:
    action = _action(config)
    payload = _payload(config)
    plan = copy.deepcopy(dict(state.get("plan") or {}))
    completed = list(state.get("completed_concepts") or [])
    completed_set = set(completed)
    current = state.get("current_concept")
    results = list(state.get("assessment_results") or [])
    latest = results[-1] if results else None
    recommended = state.get("recommended_action") or "teach_concept"

    if action == ACTION_ADVANCE:
        target = concept_by_title_or_id(plan, payload.get("target_concept_id")) or concept_by_id(plan, current)
        if target is not None:
            target_id = str(target["id"])
            _set_concept_status(plan, target_id, ConceptStatus.COMPLETED)
            if target_id not in completed_set:
                completed.append(target_id)
                completed_set.add(target_id)
            order = int(target.get("order") or 0)
            next_id = _next_concept_id(plan, completed_set, after_order=order)
            current = next_id
            if next_id:
                _set_concept_status(plan, next_id, ConceptStatus.IN_PROGRESS)
                recommended = "teach_concept"
            else:
                recommended = "lesson_complete"

    elif action == ACTION_REVISIT:
        target = concept_by_title_or_id(plan, payload.get("concept_id")) or concept_by_id(plan, current)
        if target is not None:
            target_id = str(target["id"])
            _set_concept_status(plan, target_id, ConceptStatus.IN_PROGRESS)
            if target_id in completed_set:
                completed.remove(target_id)
                completed_set.discard(target_id)
            current = target_id
        recommended = "reteach_concept"

    elif latest is not None:
        if bool(latest.get("correct")):
            target = concept_by_title_or_id(plan, str(latest.get("concept") or "")) or concept_by_id(plan, current)
            if target is not None:
                target_id = str(target["id"])
                _set_concept_status(plan, target_id, ConceptStatus.COMPLETED)
                if target_id not in completed_set:
                    completed.append(target_id)
                    completed_set.add(target_id)
                next_id = _next_concept_id(
                    plan, completed_set, after_order=int(target.get("order") or 0)
                )
                current = next_id
                if next_id:
                    _set_concept_status(plan, next_id, ConceptStatus.IN_PROGRESS)
                    recommended = "teach_concept"
                else:
                    recommended = "lesson_complete"
        else:
            if current:
                _set_concept_status(plan, current, ConceptStatus.IN_PROGRESS)
            recommended = "reteach_concept"

    pending = [
        str(concept["id"])
        for concept in plan.get("concepts", [])
        if concept.get("id") and str(concept["id"]) not in completed_set
    ]
    return {
        "plan": plan,
        "completed_concepts": completed,
        "pending_concepts": pending,
        "current_concept": current,
        "recommended_action": recommended,
        "understanding_score": round(clamp(state.get("understanding_score") or 0.0), 4),
        "last_updated": now_iso(),
    }


async def persist_observations_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    action = _action(config)
    payload = _payload(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")

    observation = await _record_evidence(
        db,
        lesson=lesson,
        state=state,
        action=action,
        payload=payload,
        store=_store(config),
    )
    if observation is not None:
        logger.debug(
            "recorded %s observation for lesson=%s concept=%s",
            observation.get("kind"),
            lesson.id,
            observation.get("concept"),
        )

    await _upsert_plan_row(db, lesson.id, dict(state.get("plan") or {}))
    elapsed, remaining = _timing(lesson, state)
    await _sync_lesson_row(db, lesson, state, elapsed=elapsed, remaining=remaining)
    await _upsert_checkpoint_index(db, lesson.id, {**state, "last_updated": now_iso()})
    await db.flush()
    board_revision = int(lesson.board_revision or 0)
    return {"last_updated": now_iso(), "board_revision": board_revision}


async def finalize_lesson_node(state: LessonState, config: Any) -> dict[str, Any]:
    db = _db(config)
    payload = _payload(config)
    lesson = await db.get(Lesson, state["lesson_id"])
    if lesson is None:
        raise ValueError(f"lesson {state.get('lesson_id')} does not exist")

    elapsed, _remaining = _timing(lesson, state)
    plan = copy.deepcopy(dict(state.get("plan") or {}))
    completed = list(state.get("completed_concepts") or [])
    total_concepts = len(plan.get("concepts") or [])
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = (
            f"Lesson on '{state.get('topic') or lesson.topic}' covered "
            f"{len(completed)} of {total_concepts} planned concepts in "
            f"{max(1, elapsed // 60)} minutes."
        )
    observation = await memory_service.record_observation(
        db,
        student_id=state.get("student_id") or lesson.student_id,
        lesson_id=lesson.id,
        subject=state.get("subject") or lesson.subject or "General Studies",
        concept=", ".join(
            _concept_title(plan, concept_id) for concept_id in completed[:3]
        )
        or str(state.get("topic") or lesson.topic),
        observation=f"Lesson summary: {summary[:400]}",
        kind="lesson_summary",
    )
    store = _store(config)
    if observation is not None and store is not None:
        await memory_service.store_put_memory(
            store,
            student_id=state.get("student_id") or lesson.student_id,
            observation=observation,
        )

    final_state: LessonState = {
        **state,
        "status": LessonStatus.COMPLETED,
        "recommended_action": "lesson_complete",
        "remaining_seconds": 0,
        "last_updated": now_iso(),
    }
    await _sync_lesson_row(db, lesson, final_state, elapsed=elapsed, remaining=0, finalize=True)
    await _upsert_checkpoint_index(db, lesson.id, final_state)
    await db.flush()

    # Board: force one snapshot at session end (§8) — best-effort, never fatal.
    snapshot_ref: str | None = state.get("board_snapshot_ref")
    try:
        from app.board.service import BoardService

        envelope = await BoardService().create_snapshot(db, lesson.id, force=True)
        snapshot_ref = str(envelope.get("revision") or "")
    except Exception:  # pragma: no cover - board might be unavailable/empty
        logger.warning("board snapshot on lesson finish failed lesson=%s", lesson.id, exc_info=True)

    return {
        "plan": plan,
        "completed_concepts": completed,
        "pending_concepts": [
            str(concept["id"])
            for concept in plan.get("concepts", [])
            if concept.get("id") and str(concept["id"]) not in set(completed)
        ],
        "status": LessonStatus.COMPLETED,
        "recommended_action": "lesson_complete",
        "remaining_seconds": 0,
        "elapsed_seconds": elapsed,
        "board_snapshot_ref": snapshot_ref,
        "last_updated": now_iso(),
    }


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


def _route_after_retrieve(state: LessonState, config: Any) -> str:
    action = _action(config)
    plan = state.get("plan") or {}
    if action != ACTION_START and planner.plan_is_usable(plan):
        return "load"
    if action == ACTION_LOAD and planner.plan_is_usable(plan):
        return "load"
    return "plan"


def _route_after_teach(state: LessonState, config: Any) -> str:
    action = _action(config)
    if action == ACTION_ANSWER:
        return "record"
    if action in {ACTION_ADVANCE, ACTION_REVISIT}:
        return "advance"
    if action == ACTION_FINISH:
        return "finish"
    return "persist"


# ---------------------------------------------------------------------------
# graph factory
# ---------------------------------------------------------------------------


def build_lesson_graph(checkpointer: Any = None, store: Any = None) -> Any:
    """Compile the lesson StateGraph (optionally with checkpoint + Store)."""
    builder = StateGraph(LessonState)
    builder.add_node("retrieve_memory", retrieve_memory_node)
    builder.add_node("plan_lesson", plan_lesson_node)
    builder.add_node("load_plan", load_plan_node)
    builder.add_node("teach_step", teach_step_node)
    builder.add_node("record_answer", record_answer_node)
    builder.add_node("evaluate_and_advance", evaluate_and_advance_node)
    builder.add_node("persist_observations", persist_observations_node)
    builder.add_node("finalize_lesson", finalize_lesson_node)

    builder.set_entry_point("retrieve_memory")
    builder.add_conditional_edges(
        "retrieve_memory", _route_after_retrieve, {"plan": "plan_lesson", "load": "load_plan"}
    )
    builder.add_edge("plan_lesson", "teach_step")
    builder.add_edge("load_plan", "teach_step")
    builder.add_conditional_edges(
        "teach_step",
        _route_after_teach,
        {
            "record": "record_answer",
            "advance": "evaluate_and_advance",
            "finish": "finalize_lesson",
            "persist": "persist_observations",
        },
    )
    builder.add_edge("record_answer", "evaluate_and_advance")
    builder.add_edge("evaluate_and_advance", "persist_observations")
    builder.add_edge("persist_observations", END)
    builder.add_edge("finalize_lesson", END)

    return builder.compile(checkpointer=checkpointer, store=store)


def graph_config(
    lesson_id: str,
    *,
    db: Any,
    store: Any = None,
    action: str = ACTION_TEACH,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the RunnableConfig used by :func:`build_lesson_graph` invocations."""
    return {
        "configurable": {
            "thread_id": lesson_id,
            "db": db,
            "store": store,
            "action": action if action in VALID_ACTIONS else ACTION_TEACH,
            "action_payload": dict(payload or {}),
        }
    }


def teaching_state_of(state: LessonState) -> dict[str, Any]:
    """Public helper: TeachingState DTO for a normalized state."""
    return teaching_state(normalize_state(state))


__all__ = [
    "ACTION_ADVANCE",
    "ACTION_ANSWER",
    "ACTION_ASK_QUESTION",
    "ACTION_FINISH",
    "ACTION_LOAD",
    "ACTION_REVISIT",
    "ACTION_START",
    "ACTION_TEACH",
    "ACTION_UPDATE_PROGRESS",
    "VALID_ACTIONS",
    "build_lesson_graph",
    "evaluate_and_advance_node",
    "finalize_lesson_node",
    "graph_config",
    "load_plan_node",
    "persist_observations_node",
    "plan_lesson_node",
    "record_answer_node",
    "retrieve_memory_node",
    "teach_step_node",
    "teaching_state_of",
]
