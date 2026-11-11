"""Seed a demo student and a finished lesson with board, transcript and notes.

Run from ``backend/`` with the project venv::

    ..\\.venv\\Scripts\\python.exe -m scripts.seed_demo

Idempotent enough for local demos: reuses the demo account, adds a fresh lesson
each run, and generates structured notes for it (offline generator when no
notes/Qwen API key is configured).
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from app.config import get_settings
from app.contracts import dtos
from app.db import repositories
from app.db.base import utcnow
from app.db.models import LessonBoardEvent, LessonBoardSnapshot
from app.db.session import init_models, session_scope
from app.core.security import hash_password
from app.lessons import service as lessons_service
from app.lessons.transcripts import append_transcript_entry
from app.notes.service import request_note_generation, run_generation

DEMO_EMAIL = "demo@classroom.local"
DEMO_PASSWORD = "classroom123"


def _event(
    lesson_id: str,
    sequence: int,
    event_type: str,
    element_id: str,
    payload: dict,
) -> LessonBoardEvent:
    return LessonBoardEvent(
        lesson_id=lesson_id,
        event_id=f"evt_demo_{lesson_id[-6:]}_{sequence:03d}",
        sequence=sequence,
        type=event_type,
        element_id=element_id,
        payload=payload,
        board_revision=sequence,
        depends_on=[],
        source="qwen_tool",
        animate=False,
    )


def _demo_board(lesson_id: str) -> list[LessonBoardEvent]:
    heading = "Introduction to Differentiation"
    return [
        _event(
            lesson_id,
            1,
            "write_heading",
            "el_demo_heading",
            {
                "text": heading,
                "position": {"x": 0.06, "y": 0.06},
                "level": 1,
            },
        ),
        _event(
            lesson_id,
            2,
            "write_text",
            "el_demo_intro",
            {
                "text": "A derivative measures how a function changes as its input changes.",
                "style": "body",
                "position": {"x": 0.06, "y": 0.16},
                "color": "primary",
            },
        ),
        _event(
            lesson_id,
            3,
            "write_definition",
            "el_demo_definition",
            {
                "term": "Derivative",
                "text": "The instantaneous rate of change of a function at a point.",
                "position": {"x": 0.06, "y": 0.26},
                "color": "blue",
            },
        ),
        _event(
            lesson_id,
            4,
            "write_equation",
            "el_demo_equation",
            {
                "latex": "f'(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}",
                "position": {"x": 0.06, "y": 0.36},
                "caption": "Limit definition of the derivative",
            },
        ),
        _event(
            lesson_id,
            5,
            "write_bullet_list",
            "el_demo_steps",
            {
                "items": [
                    "Differentiate powers with the power rule.",
                    "Use the limit definition for first principles.",
                    "Apply derivatives to rates of change.",
                ],
                "position": {"x": 0.06, "y": 0.52},
                "style": "numbered",
            },
        ),
    ]


def _snapshot_document(lesson_id: str, events: list[LessonBoardEvent]) -> dict:
    now = utcnow().isoformat()
    elements = []
    for event in events:
        elements.append(
            {
                "element_id": event.element_id,
                "type": event.type,
                "level": 2 if event.type != "write_equation" else 3,
                "payload": event.payload,
                "position": (event.payload or {}).get("position"),
                "size": None,
                "style": (event.payload or {}).get("style"),
                "color": (event.payload or {}).get("color"),
                "created_sequence": event.sequence,
                "updated_sequence": None,
                "removed_at_sequence": None,
                "animation": False,
            }
        )
    return {
        "session_id": lesson_id,
        "revision": len(events),
        "last_sequence": len(events),
        "elements": elements,
        "created_at": now,
        "updated_at": now,
    }


async def main() -> None:
    settings = get_settings()
    await init_models()
    print(f"Seeding demo data into {settings.database_url}")

    note_id: str | None = None
    lesson_id: str | None = None

    async with session_scope() as db:
        student = await repositories.get_user_by_email(db, DEMO_EMAIL)
        if student is None:
            student = await repositories.create_user(
                db,
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                display_name="Demo Student",
            )
            print(f"Created demo student {DEMO_EMAIL}")
        else:
            print(f"Reusing demo student {DEMO_EMAIL}")
        student_id = student.id

        lesson = await lessons_service.create_lesson(
            db,
            student,
            dtos.CreateLessonRequest(
                topic="Introduction to Differentiation",
                subject="Mathematics",
                learning_level="high_school",
                duration_minutes=20,
            ),
        )
        lesson_id = lesson.id

        turns = [
            ("tutor", "Welcome! Today we are going to look at differentiation from first principles."),
            ("tutor", "A derivative measures how a function changes as its input changes."),
            ("student", "Is the derivative the same as the gradient of the tangent line?"),
            ("tutor", "Exactly. The derivative is the instantaneous rate of change, which is the gradient of the tangent at that point."),
            ("tutor", "The limit definition lets us compute it exactly instead of estimating from a graph."),
            ("student", "So for f of x equals x squared, the derivative is two x?"),
            ("tutor", "Correct. Differentiate powers with the power rule, then use the limit definition for first principles."),
            ("system", "Lesson finished by student."),
        ]
        for index, (speaker, text) in enumerate(turns):
            await append_transcript_entry(
                db,
                lesson.id,
                speaker,
                text,
                started_at=utcnow() - timedelta(minutes=len(turns) - index),
            )

        events = _demo_board(lesson.id)
        for event in events:
            db.add(event)
        db.add(
            LessonBoardSnapshot(
                lesson_id=lesson.id,
                revision=len(events),
                last_sequence=len(events),
                document=_snapshot_document(lesson.id, events),
            )
        )
        lesson.board_revision = len(events)
        lesson.last_board_sequence = len(events)
        lesson.started_at = utcnow() - timedelta(minutes=18)
        lesson.progress = 1.0
        await db.flush()

        lesson = await lessons_service.end_lesson(db, lesson, reason="demo_seed")
        note = await request_note_generation(db, lesson)
        note_id = note.id

    if note_id is not None:
        await run_generation(note_id)

    async with session_scope() as db:
        note = await repositories.get_note(db, note_id) if note_id else None
        status = note.status if note is not None else "missing"
        title = note.title if note is not None else ""
    print("Demo data ready")
    print(f"  email:    {DEMO_EMAIL}")
    print(f"  password: {DEMO_PASSWORD}")
    print(f"  lesson:   {lesson_id}")
    print(f"  note:     {note_id} ({status}) {title}")
    print(json.dumps({"email": DEMO_EMAIL, "password": DEMO_PASSWORD,
                      "lesson_id": lesson_id, "note_id": note_id}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
