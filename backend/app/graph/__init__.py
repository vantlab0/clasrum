"""LangGraph lesson orchestration: state, persistence, planning, memory, prompts.

Public entry point used by the REST layer and the tool registry is
:mod:`app.graph.service`.
"""
from __future__ import annotations

from app.graph.checkpointing import (
    configured_kind,
    init_persistence,
    lesson_persistence,
    shutdown_persistence,
)
from app.graph.planner import generate_plan, infer_subject
from app.graph.prompts import build_realtime_instructions, build_teacher_system_prompt
from app.graph.state import (
    LessonPlan,
    LessonState,
    normalize_state,
    teaching_state,
)

__all__ = [
    "LessonPlan",
    "LessonState",
    "build_realtime_instructions",
    "build_teacher_system_prompt",
    "configured_kind",
    "generate_plan",
    "infer_subject",
    "init_persistence",
    "lesson_persistence",
    "normalize_state",
    "shutdown_persistence",
    "teaching_state",
]
