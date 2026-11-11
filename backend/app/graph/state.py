"""``LessonState`` — the LangGraph teaching-state contract.

Authority: ``docs/CONTRACTS.md`` §9. The state is a JSON-safe ``TypedDict``; the
LangGraph checkpoint (``thread_id == lesson_id``) is the authoritative store,
while ``lesson_checkpoints.state`` in the relational DB is only a resume index
(read-back fallback when checkpoints are unavailable, e.g. in-memory mode).

Everything in this module is pure: helpers mutate copies and clamp values so a
corrupt/legacy state can never produce an out-of-range DTO.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, NotRequired, TypedDict

from app.db.constants import ConceptStatus, LessonStatus

MASTERY_MIN = 0
MASTERY_MAX = 5
MAX_ASSESSMENT_RESULTS = 200

# Mastery status vocabulary shared with app.graph.memory.
STATUS_NOT_STARTED = "not_started"
STATUS_LEARNING = "learning"
STATUS_NEEDS_PRACTICE = "needs_practice"
STATUS_MASTERED = "mastered"
MASTERY_STATUSES = frozenset(
    {STATUS_NOT_STARTED, STATUS_LEARNING, STATUS_NEEDS_PRACTICE, STATUS_MASTERED}
)


class PlanConcept(TypedDict, total=False):
    """One concept of a lesson plan (also serialised into ``lesson_plans``)."""

    id: str
    title: str
    order: int
    status: str  # pending | in_progress | completed
    objectives: list[str]
    estimated_minutes: NotRequired[int]


class TimingBudget(TypedDict, total=False):
    total_minutes: int
    warmup_minutes: int
    per_concept_minutes: list[int]
    wrapup_minutes: int


class AssessmentCheckpoint(TypedDict, total=False):
    concept_id: str
    prompt: str
    expected_answer: NotRequired[str]


class LessonPlan(TypedDict, total=False):
    """LessonPlan DTO payload (contract §5) plus additive planning metadata."""

    objectives: list[str]
    concepts: list[PlanConcept]
    timing_budget: TimingBudget
    assessment_checkpoints: list[AssessmentCheckpoint]
    generated_by: str
    topic: str
    subject: str


class AssessmentResult(TypedDict, total=False):
    """One evaluated answer kept in state for progress/mastery calculation."""

    question_id: str | None
    concept: str
    question: str
    expected_answer: str | None
    student_answer: str
    correct: bool
    feedback: str | None
    mastery_delta: int
    at: str


class StudentMemory(TypedDict, total=False):
    """Compact, prompt-safe long-term memory snapshot (see app.graph.memory)."""

    profile: dict[str, Any]
    mastery: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    prior_difficulty: list[str]
    subjects_studied: list[str]
    lesson_history: list[dict[str, Any]]


class LessonState(TypedDict, total=False):
    """The teaching state (contract §9, verbatim keys)."""

    lesson_id: str
    student_id: str
    topic: str
    subject: str
    learning_level: str
    duration_minutes: int
    plan: LessonPlan
    current_concept: str | None
    completed_concepts: list[str]
    pending_concepts: list[str]
    understanding_score: float
    assessment_results: list[AssessmentResult]
    student_memory: StudentMemory
    board_revision: int
    board_snapshot_ref: str | None
    elapsed_seconds: int
    remaining_seconds: int
    status: str
    recommended_action: str | None
    last_updated: str


def now_iso() -> str:
    """RFC 3339 UTC with milliseconds (contract §1)."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return low
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return low
    return max(low, min(high, numeric))


def clamp_mastery(value: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return MASTERY_MIN
    return max(MASTERY_MIN, min(MASTERY_MAX, numeric))


def new_lesson_state(
    *,
    lesson_id: str,
    student_id: str,
    topic: str,
    subject: str,
    learning_level: str,
    duration_minutes: int,
) -> LessonState:
    """Build a fresh, fully-populated state for a brand new lesson."""
    minutes = max(1, min(int(duration_minutes or 20), 240))
    return LessonState(
        lesson_id=str(lesson_id),
        student_id=str(student_id),
        topic=str(topic),
        subject=str(subject or ""),
        learning_level=str(learning_level or "high_school"),
        duration_minutes=minutes,
        plan=LessonPlan(objectives=[], concepts=[], generated_by="deterministic"),
        current_concept=None,
        completed_concepts=[],
        pending_concepts=[],
        understanding_score=0.0,
        assessment_results=[],
        student_memory=StudentMemory(),
        board_revision=0,
        board_snapshot_ref=None,
        elapsed_seconds=0,
        remaining_seconds=minutes * 60,
        status=LessonStatus.CREATED,
        recommended_action=None,
        last_updated=now_iso(),
    )


def state_from_lesson(lesson: Any) -> LessonState:
    """Build a minimal state from a ``Lesson`` ORM row (pre-graph bootstrap)."""
    return new_lesson_state(
        lesson_id=lesson.id,
        student_id=lesson.student_id,
        topic=lesson.topic,
        subject=lesson.subject or "",
        learning_level=lesson.learning_level,
        duration_minutes=lesson.duration_minutes,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _known_strings(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if isinstance(item, (str, int))]


def normalize_state(raw: Any) -> LessonState:
    """Coerce an arbitrary persisted value into a valid :class:`LessonState`."""
    data = _as_dict(raw)
    duration = data.get("duration_minutes", 20)
    try:
        duration_minutes = max(1, min(int(duration), 240))
    except (TypeError, ValueError):
        duration_minutes = 20

    status = str(data.get("status") or LessonStatus.CREATED)
    if status not in LessonStatus.ALL:
        status = LessonStatus.CREATED

    plan_raw = _as_dict(data.get("plan"))
    plan = _normalize_plan(plan_raw)
    completed = _known_strings(data.get("completed_concepts"))
    pending = _known_strings(data.get("pending_concepts"))
    known_concept_ids = [concept.get("id") for concept in plan.get("concepts", []) if concept.get("id")]
    completed = [item for item in completed if item in set(known_concept_ids)]
    pending = [item for item in pending if item in set(known_concept_ids)]
    current = data.get("current_concept")
    current = str(current) if current else None
    if current is not None and current not in set(known_concept_ids):
        current = None

    assessment_results: list[AssessmentResult] = []
    for item in _as_list(data.get("assessment_results"))[-MAX_ASSESSMENT_RESULTS:]:
        if isinstance(item, dict):
            assessment_results.append(dict(item))

    try:
        elapsed = max(0, int(data.get("elapsed_seconds") or 0))
    except (TypeError, ValueError):
        elapsed = 0
    try:
        remaining = max(0, int(data.get("remaining_seconds", duration_minutes * 60)))
    except (TypeError, ValueError):
        remaining = duration_minutes * 60
    try:
        board_revision = max(0, int(data.get("board_revision") or 0))
    except (TypeError, ValueError):
        board_revision = 0

    snapshot_ref = data.get("board_snapshot_ref")
    recommended = data.get("recommended_action")

    return LessonState(
        lesson_id=str(data.get("lesson_id") or ""),
        student_id=str(data.get("student_id") or ""),
        topic=str(data.get("topic") or ""),
        subject=str(data.get("subject") or ""),
        learning_level=str(data.get("learning_level") or "high_school"),
        duration_minutes=duration_minutes,
        plan=plan,
        current_concept=current,
        completed_concepts=completed,
        pending_concepts=pending,
        understanding_score=clamp(data.get("understanding_score") or 0.0),
        assessment_results=assessment_results,
        student_memory=StudentMemory(**_as_dict(data.get("student_memory"))),
        board_revision=board_revision,
        board_snapshot_ref=str(snapshot_ref) if snapshot_ref else None,
        elapsed_seconds=elapsed,
        remaining_seconds=remaining,
        status=status,
        recommended_action=str(recommended) if recommended else None,
        last_updated=str(data.get("last_updated") or now_iso()),
    )


def _normalize_plan(raw: dict[str, Any]) -> LessonPlan:
    concepts: list[PlanConcept] = []
    for index, item in enumerate(_as_list(raw.get("concepts"))):
        if not isinstance(item, dict):
            continue
        concept_id = str(item.get("id") or f"concept_{index + 1}")
        status = str(item.get("status") or ConceptStatus.PENDING)
        if status not in {ConceptStatus.PENDING, ConceptStatus.IN_PROGRESS, ConceptStatus.COMPLETED}:
            status = ConceptStatus.PENDING
        concept: PlanConcept = PlanConcept(
            id=concept_id,
            title=str(item.get("title") or concept_id),
            order=int(item.get("order") or index + 1),
            status=status,
            objectives=_known_strings(item.get("objectives")),
        )
        estimated = item.get("estimated_minutes")
        if isinstance(estimated, int):
            concept["estimated_minutes"] = estimated
        concepts.append(concept)
    plan = LessonPlan(
        objectives=_known_strings(raw.get("objectives")),
        concepts=concepts,
        generated_by=str(raw.get("generated_by") or "deterministic"),
    )
    timing = _as_dict(raw.get("timing_budget"))
    if timing:
        plan["timing_budget"] = TimingBudget(**timing)  # type: ignore[typeddict-item]
    checkpoints = [dict(item) for item in _as_list(raw.get("assessment_checkpoints")) if isinstance(item, dict)]
    if checkpoints:
        plan["assessment_checkpoints"] = checkpoints  # type: ignore[typeddict-item]
    if raw.get("topic"):
        plan["topic"] = str(raw["topic"])
    if raw.get("subject"):
        plan["subject"] = str(raw["subject"])
    return plan


# ---------------------------------------------------------------------------
# derived views
# ---------------------------------------------------------------------------


def concept_by_id(plan: LessonPlan | dict[str, Any], concept_id: str | None) -> PlanConcept | None:
    if not concept_id:
        return None
    for concept in (plan or {}).get("concepts", []):
        if concept.get("id") == concept_id:
            return concept
    return None


def concept_by_title_or_id(
    plan: LessonPlan | dict[str, Any], needle: str | None
) -> PlanConcept | None:
    if not needle:
        return None
    needle = str(needle).strip()
    for concept in (plan or {}).get("concepts", []):
        if needle in {concept.get("id"), concept.get("title")}:
            return concept
    lowered = needle.lower()
    for concept in (plan or {}).get("concepts", []):
        title = str(concept.get("title") or "").lower()
        if title and (lowered in title or title in lowered):
            return concept
    return None


def compute_progress(state: LessonState) -> float:
    concepts = (state.get("plan") or {}).get("concepts") or []
    if not concepts:
        return 0.0
    completed = set(state.get("completed_concepts") or [])
    done = sum(1 for concept in concepts if concept.get("id") in completed)
    return round(clamp(done / len(concepts)), 4)


def derive_recommended_action(state: LessonState) -> str:
    if state.get("status") == LessonStatus.COMPLETED:
        return "lesson_complete"
    current = state.get("current_concept")
    if not current:
        return "lesson_complete" if not state.get("pending_concepts") else "review"
    latest = (state.get("assessment_results") or [])[-1] if state.get("assessment_results") else None
    if latest and latest.get("correct") is False:
        return "reteach_concept"
    if latest and latest.get("correct") is True:
        return "advance_concept"
    return "teach_concept"


def status_for_mastery(mastery_level: int) -> str:
    """Map a 0..5 mastery level to the contract status vocabulary."""
    level = clamp_mastery(mastery_level)
    if level <= 0:
        return STATUS_NOT_STARTED
    if level >= MASTERY_MAX:
        return STATUS_MASTERED
    if level <= 2:
        return STATUS_NEEDS_PRACTICE
    return STATUS_LEARNING


def teaching_state(state: LessonState) -> dict[str, Any]:
    """TeachingState DTO (contract §5) derived from the graph state."""
    plan = state.get("plan") or {}
    current = concept_by_id(plan, state.get("current_concept"))
    concepts = plan.get("concepts") or []
    return {
        "current_concept_id": state.get("current_concept"),
        "current_concept_title": (current or {}).get("title"),
        "completed_concept_ids": list(state.get("completed_concepts") or []),
        "pending_concept_ids": list(state.get("pending_concepts") or []),
        "progress": compute_progress(state),
        "understanding_score": round(clamp(state.get("understanding_score") or 0.0), 4),
        "assessment_results": list(state.get("assessment_results") or []),
        "recommended_action": state.get("recommended_action")
        or derive_recommended_action(state),
        "is_finished": state.get("status") == LessonStatus.COMPLETED
        or (bool(concepts) and compute_progress(state) >= 1.0),
        "remaining_seconds": int(state.get("remaining_seconds") or 0),
        "elapsed_seconds": int(state.get("elapsed_seconds") or 0),
        "status": state.get("status") or LessonStatus.CREATED,
    }


__all__ = [
    "AssessmentCheckpoint",
    "AssessmentResult",
    "ConceptStatus",
    "LessonPlan",
    "LessonState",
    "MASTERY_MAX",
    "MASTERY_MIN",
    "MASTERY_STATUSES",
    "PlanConcept",
    "STATUS_LEARNING",
    "STATUS_MASTERED",
    "STATUS_NEEDS_PRACTICE",
    "STATUS_NOT_STARTED",
    "StudentMemory",
    "TimingBudget",
    "clamp",
    "clamp_mastery",
    "compute_progress",
    "concept_by_id",
    "concept_by_title_or_id",
    "derive_recommended_action",
    "new_lesson_state",
    "normalize_state",
    "now_iso",
    "state_from_lesson",
    "status_for_mastery",
    "teaching_state",
]
