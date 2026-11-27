"""Every lesson gets fresh, lesson-local memory only."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db.models import (
    StudentConceptMastery,
    StudentLearningObservation,
    StudentLearningProfile,
)
from app.db.session import session_scope
from app.graph.memory import retrieve_memory
from app.graph.planner import generate_plan
from app.graph.prompts import build_teacher_system_prompt
from app.realtime.hub import _teacher_instructions


@pytest.mark.asyncio
async def test_retrieve_memory_only_reads_current_lesson(seeded_student, make_lesson):
    old_lesson = await make_lesson(
        seeded_student, topic="Photosynthesis", subject="Biology"
    )
    new_lesson = await make_lesson(
        seeded_student, topic="Fractions", subject="Math"
    )

    async with session_scope() as db:
        profile = (
            await db.execute(
                select(StudentLearningProfile).where(
                    StudentLearningProfile.student_id == seeded_student["id"]
                )
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = StudentLearningProfile(student_id=seeded_student["id"])
            db.add(profile)
        profile.level = "high_school"
        profile.strengths = ["photosynthesis"]
        profile.weaknesses = ["fractions"]
        profile.subjects_studied = ["Biology"]
        db.add(
            StudentConceptMastery(
                student_id=seeded_student["id"],
                subject="Biology",
                concept="chlorophyll",
                mastery_level=1,
                status="needs_practice",
                attempts=4,
            )
        )
        db.add(
            StudentLearningObservation(
                student_id=seeded_student["id"],
                lesson_id=old_lesson["id"],
                subject="Biology",
                concept="chlorophyll",
                observation="old lesson observation about chlorophyll",
            )
        )
        db.add(
            StudentLearningObservation(
                student_id=seeded_student["id"],
                lesson_id=new_lesson["id"],
                subject="Math",
                concept="fractions",
                observation="new lesson observation about fractions",
            )
        )
        await db.flush()

        memory = await retrieve_memory(
            db,
            seeded_student["id"],
            subject="Math",
            lesson_id=new_lesson["id"],
        )

    assert [item["observation"] for item in memory["observations"]] == [
        "new lesson observation about fractions"
    ]
    assert memory["profile"] == {}
    assert memory["mastery"] == []
    assert memory["prior_difficulty"] == []
    assert memory["subjects_studied"] == []
    assert memory["lesson_history"] == []


def test_plan_ignores_prior_difficulty_from_other_topics():
    plan = generate_plan(
        topic="Photosynthesis",
        subject="Biology",
        learning_level="high_school",
        duration_minutes=20,
        student_memory={
            "mastery": [
                {
                    "concept": "long division",
                    "subject": "Math",
                    "status": "needs_practice",
                    "mastery_level": 1,
                    "attempts": 5,
                }
            ],
            "prior_difficulty": ["long division"],
        },
    )

    assert all("warmup" not in concept["id"] for concept in plan["concepts"])
    assert "long division" not in json.dumps(plan, ensure_ascii=False)


def test_teacher_prompt_does_not_surface_prior_topic_memory():
    prompt = build_teacher_system_prompt(
        lesson_id="lesson-new",
        topic="Photosynthesis",
        subject="Biology",
        learning_level="high_school",
        duration_minutes=20,
        student_memory={
            "profile": {"strengths": ["fractions"]},
            "mastery": [{"concept": "long division", "status": "needs_practice"}],
            "lesson_history": [{"topic": "Fractions"}],
            "observations": [
                {"observation": "student struggled with long division"}
            ],
        },
    )

    assert "THIS lesson only" in prompt
    assert "No memory carried in from other lessons." not in prompt
    assert "long division" in prompt  # only as a lesson-local observation fixture
    assert "fractions" not in prompt
    assert "Fractions" not in prompt


def test_realtime_instructions_require_title_then_diagram():
    instructions = _teacher_instructions(
        {"topic": "Photosynthesis", "subject": "Biology", "learning_level": "high_school", "plan": {}}
    )

    lowered = instructions.lower()
    assert "lesson title first" in lowered
    assert "immediately after the title" in lowered
    assert lowered.index("create_board_visual") > lowered.index("lesson title first")
    assert "before any definition or explanation lines" in lowered
    assert "cue_phrases" in lowered
