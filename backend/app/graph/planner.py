"""Deterministic lesson planning: topic + level + duration + memory -> plan.

The tutor must work with **no LLM and no network**: the realtime model only
narrates and draws, while the pedagogy skeleton (concepts, objectives, timing,
assessment checkpoints) is generated here, deterministically, from the
lesson request and the student's retrieved long-term memory.

Plan shape (see ``app.graph.state``):

* ``objectives``            3-5 lesson-level learning objectives
* ``concepts``              3-6 concepts with stable ids, titles, objectives,
                            ``order`` and ``status`` (contract §5 LessonPlan DTO)
* ``timing_budget``         additive metadata: warm-up / per-concept / wrap-up
* ``assessment_checkpoints`` additive metadata: one check per concept

Stable concept ids (e.g. ``c1_intuition``) make resume and idempotent planning
safe: regenerating a plan for the same lesson yields identical ids, so a
replayed ``lesson_next_concept`` cannot corrupt the teaching state.

No secrets, no I/O, no network: this module is pure and unit-testable offline.
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.graph.state import (
    AssessmentCheckpoint,
    LessonPlan,
    PlanConcept,
    StudentMemory,
    TimingBudget,
)
from app.db.constants import ConceptStatus

# ---------------------------------------------------------------------------
# subject inference
# ---------------------------------------------------------------------------

SUBJECTS = (
    "Mathematics",
    "Physics",
    "Chemistry",
    "Biology",
    "Computer Science",
    "English",
    "General Studies",
)

_SUBJECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Mathematics": (
        "math", "maths", "mathematics", "algebra", "equation", "calculus", "derivative",
        "differentiat", "integral", "integrat", "function", "geometry", "trigonometr",
        "sine", "cosine", "tangent", "probability", "statistic", "matrix", "matrices",
        "vector", "polynomial", "fraction", "decimal", "percent", "ratio", "quadratic",
        "linear", "slope", "gradient", "graph", "plot", "limit", "logarithm", "log ",
        "sequence", "series", "pythagor", "triangle", "area", "volume", "circle",
        "angle", "number", "arithmetic", "prime", "factor", "set theory", "inequality",
        "parabola", "exponential", "asymptote", "permutation", "combination",
    ),
    "Physics": (
        "physics", "force", "motion", "velocity", "speed", "acceleration", "newton",
        "energy", "momentum", "gravity", "gravitation", "electric", "circuit", "wave",
        "optics", "light", "thermodynamic", "heat", "friction", "mass", "current",
        "voltage", "resistance", "magnet", "pressure", "work done", "power", "kinematic",
        "projectile", "pendulum", "lens", "mirror", "refraction", "relativity",
    ),
    "Chemistry": (
        "chemistry", "atom", "molecule", "reaction", "acid", "base", "mole", "molar",
        "bond", "periodic", "organic", "stoichiometr", "enthalpy", "solution", "oxidation",
        "reduction", "electron", "ion", "compound", "chemical", "ph ", "titration",
        "equilibrium", "hydrocarbon", "polymer", "isotope", "valency", "catalyst",
    ),
    "Biology": (
        "biology", "cell", "dna", "rna", "photosynthesis", "respiration", "evolution",
        "genetic", "enzyme", "protein", "ecosystem", "blood", "organ", "neuron", "nerve",
        "mitosis", "meiosis", "bacteria", "virus", "plant", "animal", "human body",
        "digestive", "immune", "hormone", "biology", "species", "habitat", "chromosome",
    ),
    "Computer Science": (
        "programming", "python", "javascript", "java", "code", "coding", "algorithm",
        "recursion", "recursive", "loop", "array", "list", "dictionary", "data structure",
        "complexity", "big o", "sorting", "searching", "database", "sql", "api", "class",
        "object oriented", "variable", "binary", "network", "machine learning", "neural",
        "artificial intelligence", "computer", "software", "function call", "debug",
        "compiler", "operating system", "pointer", "stack", "queue", "hash", "tree", "graph theory",
    ),
    "English": (
        "english", "grammar", "essay", "literature", "poem", "poetry", "novel", "writing",
        "comprehension", "punctuation", "tense", "vocabulary", "shakespeare", "narrative",
        "paragraph", "summary writing", "letter writing",
    ),
}


def infer_subject(topic: str, declared: str | None = None) -> str:
    """Infer a canonical subject from explicit metadata or topic keywords.

    An explicit ``declared`` subject wins when it normalises to a known subject;
    otherwise keyword frequency decides; unknown topics fall back to
    ``General Studies`` so planning always succeeds.
    """
    if declared:
        cleaned = str(declared).strip()
        for subject in SUBJECTS:
            if cleaned.lower() == subject.lower():
                return subject
        for alias, subject in (
            ("math", "Mathematics"),
            ("physics", "Physics"),
            ("chem", "Chemistry"),
            ("bio", "Biology"),
            ("computer", "Computer Science"),
            ("programming", "Computer Science"),
            ("cs", "Computer Science"),
            ("english", "English"),
        ):
            if cleaned.lower().startswith(alias):
                return subject
    text = f" {str(topic or '').lower()} "
    scores: dict[str, int] = {}
    for subject, keywords in _SUBJECT_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in text:
                score += 2 if len(keyword) >= 6 else 1
        if score:
            scores[subject] = score
    if not scores:
        return "General Studies"
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0]


# ---------------------------------------------------------------------------
# concept arc
# ---------------------------------------------------------------------------

#: kind -> (title template, objective templates)
_ARC: dict[str, tuple[str, ...]] = {
    "warmup": (
        "Warm-up: revisiting {focus}",
        "Recall the prerequisite ideas behind {focus}",
        "Rebuild confidence with a short, familiar example",
    ),
    "intuition": (
        "Intuition behind {topic}",
        "Explain {topic} in everyday language before using symbols",
        "Describe one real situation where {topic} appears",
    ),
    "definition": (
        "Defining {topic} precisely",
        "State the formal definition of {topic}",
        "Identify the quantities and notation involved",
    ),
    "core": (
        "The core idea of {topic}",
        "Derive or justify the central rule of {topic}",
        "Apply the rule to a simple case and predict the result",
    ),
    "example": (
        "Worked example: {topic}",
        "Walk through a fully worked example step by step",
        "Justify each step using the definition from the previous concept",
    ),
    "practice": (
        "Practice: {topic}",
        "Solve one guided and one independent problem on {topic}",
        "Check the answer with a second method or an estimate",
    ),
    "pitfalls": (
        "Common mistakes with {topic}",
        "Recognise the most frequent errors and why they happen",
        "Correct a deliberately flawed solution",
    ),
    "extend": (
        "Extending {topic}",
        "Connect {topic} to a related idea or a harder variant",
        "Sketch when the standard approach stops working",
    ),
}

_SUBJECT_ARC: dict[str, tuple[str, ...]] = {
    "Mathematics": ("intuition", "definition", "core", "example", "practice", "pitfalls"),
    "Physics": ("intuition", "definition", "core", "example", "practice", "pitfalls"),
    "Chemistry": ("intuition", "definition", "core", "example", "practice", "pitfalls"),
    "Biology": ("intuition", "definition", "core", "example", "practice", "extend"),
    "Computer Science": ("intuition", "definition", "core", "example", "practice", "pitfalls"),
    "English": ("intuition", "definition", "example", "practice", "pitfalls", "extend"),
    "General Studies": ("intuition", "definition", "core", "example", "practice", "extend"),
}

_SUBJECT_VERBS: dict[str, tuple[str, ...]] = {
    "Mathematics": ("calculate", "justify algebraically", "estimate before computing"),
    "Physics": ("model the situation", "reason with units and vectors", "predict then verify"),
    "Chemistry": ("balance and count particles", "reason about structure and energy", "predict products"),
    "Biology": ("trace the process step by step", "relate structure to function", "compare systems"),
    "Computer Science": ("trace the execution mentally", "reason about invariants", "test edge cases"),
    "English": ("read closely", "argue with evidence from the text", "draft and revise"),
    "General Studies": ("explain clearly", "use an example", "connect to prior knowledge"),
}

_ASSESSMENT_TEMPLATES: dict[str, str] = {
    "warmup": "Quick recall: what do you already remember about {focus}?",
    "intuition": "In your own words, what does {topic} describe? Can you give one everyday example?",
    "definition": "State the definition of {topic} and name the key quantities in it.",
    "core": "Explain why the core rule of {topic} works, not just what it is.",
    "example": "Walk me through how you would solve a similar {topic} problem step by step.",
    "practice": "Try this independently: apply {topic} to a new problem and explain your reasoning.",
    "pitfalls": "Spot the mistake: what is the most common error with {topic}, and how do you avoid it?",
    "extend": "How would you extend {topic} to a harder case? What changes?",
}

_CONCEPT_COUNT_BY_DURATION = ((10, 3), (20, 4), (35, 5), (10_000, 6))


def concept_count_for(duration_minutes: int) -> int:
    minutes = max(1, int(duration_minutes or 20))
    for limit, count in _CONCEPT_COUNT_BY_DURATION:
        if minutes <= limit:
            return count
    return 6


def _slugify(text: str, *, fallback: str = "topic") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (slug or fallback)[:48]


def _level_phrasing(learning_level: str) -> str:
    level = str(learning_level or "").strip().lower()
    if level in {"middle_school", "middle-school", "middle", "ks3", "beginner"}:
        return "Use simple language, concrete examples and short sentences."
    if level in {"high_school", "high-school", "high", "secondary", "ks4", "intermediate"}:
        return "Assume secondary-school background; keep notation standard and clean."
    if level in {"undergraduate", "university", "college", "advanced", "expert"}:
        return "Assume exam-level maturity; move quickly through basics and emphasise rigour."
    return "Match the student's demonstrated level; check understanding often."


def _distribute_minutes(
    total_minutes: int, concept_count: int, *, warmup: bool
) -> tuple[TimingBudget, int]:
    total = max(5, int(total_minutes))
    warmup_minutes = max(1, round(total * 0.12)) if warmup else max(1, round(total * 0.08))
    wrapup_minutes = max(1, round(total * 0.10))
    remaining = max(concept_count, total - warmup_minutes - wrapup_minutes)
    base = remaining // concept_count
    per_concept = [base] * concept_count
    for index in range(remaining - base * concept_count):
        per_concept[index % concept_count] += 1
    budget = TimingBudget(
        total_minutes=total,
        warmup_minutes=warmup_minutes,
        per_concept_minutes=[int(value) for value in per_concept],
        wrapup_minutes=wrapup_minutes,
    )
    return budget, per_concept[0] if per_concept else 5


def _memory_focus(memory: StudentMemory | dict[str, Any] | None, subject: str) -> str | None:
    """Pick one concept the student recently struggled with (same subject first)."""
    if not isinstance(memory, dict):
        return None
    candidates: list[str] = []
    for item in memory.get("mastery") or []:
        if not isinstance(item, dict):
            continue
        item_subject = str(item.get("subject") or "")
        status = str(item.get("status") or "")
        concept = str(item.get("concept") or "").strip()
        if not concept:
            continue
        if item_subject and subject and item_subject.lower() != subject.lower():
            continue
        if status in {"needs_practice", "learning"}:
            candidates.append(concept)
    for difficulty in memory.get("prior_difficulty") or []:
        if isinstance(difficulty, str) and difficulty.strip():
            candidates.append(difficulty.strip())
    return candidates[0] if candidates else None


def generate_plan(
    *,
    topic: str,
    subject: str | None,
    learning_level: str,
    duration_minutes: int,
    student_memory: StudentMemory | dict[str, Any] | None = None,
) -> LessonPlan:
    """Generate a deterministic, memory-aware lesson plan (3-6 concepts)."""
    topic = str(topic or "").strip() or "Today's topic"
    resolved_subject = infer_subject(topic, subject)
    duration = max(1, int(duration_minutes or 20))
    count = concept_count_for(duration)
    arc = list(_SUBJECT_ARC.get(resolved_subject, _SUBJECT_ARC["General Studies"]))
    verbs = _SUBJECT_VERBS.get(resolved_subject, _SUBJECT_VERBS["General Studies"])

    focus = _memory_focus(student_memory, resolved_subject)
    has_warmup = focus is not None and duration >= 10
    if has_warmup:
        arc = ["warmup"] + arc[: max(1, count - 1)]
    else:
        arc = arc[:count]
    concept_count = len(arc)

    budget, _ = _distribute_minutes(duration, concept_count, warmup=has_warmup)
    per_concept = budget.get("per_concept_minutes") or [5] * concept_count

    concepts: list[PlanConcept] = []
    checkpoints: list[AssessmentCheckpoint] = []
    for index, kind in enumerate(arc):
        template = _ARC[kind]
        title_template = template[0]
        title = title_template.format(topic=topic, focus=focus or topic)
        objective_templates = template[1:]
        objectives = [
            objective.format(topic=topic, focus=focus or topic, verb=verbs[index % len(verbs)])
            for objective in objective_templates
        ][:2]
        objectives.append(
            f"Check understanding by having the student {verbs[index % len(verbs)]}."
        )
        concept: PlanConcept = PlanConcept(
            id=f"c{index + 1}_{kind}",
            title=title,
            order=index + 1,
            status=ConceptStatus.PENDING,
            objectives=objectives,
            estimated_minutes=int(per_concept[index]) if index < len(per_concept) else 5,
        )
        concepts.append(concept)
        checkpoints.append(
            AssessmentCheckpoint(
                concept_id=concept["id"],
                prompt=_ASSESSMENT_TEMPLATES.get(kind, "Explain {topic} in your own words.").format(
                    topic=topic, focus=focus or topic
                ),
            )
        )

    lesson_objectives = [
        f"Explain {topic} in your own words, with one example.",
        f"Apply the main rule or process of {topic} independently.",
        f"Recognise and correct common mistakes in {topic}.",
    ]
    if has_warmup and focus:
        lesson_objectives.insert(0, f"Rebuild fluency on prerequisite: {focus}.")

    return LessonPlan(
        objectives=lesson_objectives,
        concepts=concepts,
        timing_budget=budget,
        assessment_checkpoints=checkpoints,
        generated_by="deterministic",
        topic=topic,
        subject=resolved_subject,
    )


def plan_objectives_from_concepts(plan: LessonPlan | dict[str, Any]) -> list[str]:
    """Flatten concept objectives for prompts (bounded)."""
    objectives: list[str] = []
    for concept in (plan or {}).get("concepts", []):
        for objective in concept.get("objectives") or []:
            text = str(objective).strip()
            if text and text not in objectives:
                objectives.append(text)
    return objectives[:12]


def first_actionable_concept(plan: LessonPlan | dict[str, Any]) -> PlanConcept | None:
    """First concept that is not completed, in plan order."""
    concepts = list((plan or {}).get("concepts", []))
    concepts.sort(key=lambda concept: int(concept.get("order") or 0))
    for concept in concepts:
        if concept.get("status") != ConceptStatus.COMPLETED:
            return concept
    return concepts[0] if concepts else None


def plan_is_usable(plan: LessonPlan | dict[str, Any] | None) -> bool:
    """A plan is usable when it has at least one concept with an id and title."""
    if not isinstance(plan, dict):
        return False
    concepts = plan.get("concepts") or []
    return any(isinstance(concept, dict) and concept.get("id") and concept.get("title") for concept in concepts)


__all__ = [
    "SUBJECTS",
    "concept_count_for",
    "first_actionable_concept",
    "generate_plan",
    "infer_subject",
    "plan_is_usable",
    "plan_objectives_from_concepts",
]
