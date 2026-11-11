"""Shared pytest fixtures for the backend test suite.

The suite runs against a throwaway SQLite file database configured through
environment variables *before* ``app`` is imported. Every test starts from an
empty schema (``reset_models``), authenticates through the real REST API and
uses an ``httpx.AsyncClient`` bound to the ASGI app.

Collaborator modules owned by backend-ai (``app.graph.service`` and
``app.realtime.router``) are installed as deterministic fakes **only while the
real modules do not exist yet**, so the REST suite is not blocked by parallel
work. When the real modules are present the fakes are skipped and the suite
exercises the real integration.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# --- environment MUST be configured before app.config is imported ------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ai-classroom-tests-"))
_DB_PATH = _TMP_DIR / "test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production-0123456789"
os.environ["JWT_EXPIRES_MINUTES"] = "60"
os.environ["QWEN_API_KEY"] = ""
os.environ["QWEN_FAKE_MODE"] = "true"
os.environ["NOTES_API_KEY"] = ""
os.environ["CORS_ORIGINS"] = '["*"]'


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# collaborator fakes (temporary — removed automatically once real modules land)
# ---------------------------------------------------------------------------

_FAKE_GRAPH_STATES: dict[str, dict] = {}


def _install_fake_graph_service() -> None:
    module = types.ModuleType("app.graph.service")
    module.__doc__ = "Temporary deterministic stand-in for app.graph.service."

    async def start_lesson(db, lesson):  # noqa: ANN001, ANN202
        from sqlalchemy import select

        from app.db.models import LessonCheckpoint, LessonPlan

        plan = {
            "objectives": [
                f"Understand the core ideas of {lesson.topic}",
                f"Practise applying {lesson.topic} to examples",
            ],
            "concepts": [
                {
                    "id": f"{lesson.id[:8]}-c1",
                    "title": f"Foundations of {lesson.topic}",
                    "order": 0,
                    "status": "in_progress",
                    "objectives": [f"Explain what {lesson.topic} means"],
                },
                {
                    "id": f"{lesson.id[:8]}-c2",
                    "title": f"Applying {lesson.topic}",
                    "order": 1,
                    "status": "pending",
                    "objectives": [f"Solve a problem using {lesson.topic}"],
                },
            ],
        }
        state = {
            "lesson_id": lesson.id,
            "student_id": lesson.student_id,
            "topic": lesson.topic,
            "subject": lesson.subject or "general",
            "learning_level": lesson.learning_level,
            "duration_minutes": lesson.duration_minutes,
            "plan": plan,
            "current_concept": plan["concepts"][0]["id"],
            "completed_concepts": [],
            "pending_concepts": [c["id"] for c in plan["concepts"]],
            "understanding_score": 0.0,
            "assessment_results": [],
            "progress": 0.1,
            "board_revision": 0,
            "board_snapshot_ref": None,
            "elapsed_seconds": 0,
            "remaining_seconds": int(lesson.duration_minutes) * 60,
            "status": "live",
            "recommended_action": "teach_current_concept",
            "last_updated": "2026-11-11T00:00:00.000Z",
        }
        plan_row = (
            await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson.id))
        ).scalar_one_or_none()
        if plan_row is None:
            db.add(
                LessonPlan(
                    lesson_id=lesson.id,
                    objectives=plan["objectives"],
                    concepts=plan["concepts"],
                )
            )
        else:
            plan_row.objectives = plan["objectives"]
            plan_row.concepts = plan["concepts"]
        checkpoint = (
            await db.execute(
                select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson.id)
            )
        ).scalar_one_or_none()
        if checkpoint is None:
            db.add(
                LessonCheckpoint(
                    lesson_id=lesson.id,
                    langgraph_thread_id=lesson.id,
                    state=dict(state),
                )
            )
        else:
            checkpoint.state = dict(state)
        await db.flush()
        _FAKE_GRAPH_STATES[lesson.id] = state
        return {"plan": plan, "state": state}

    async def load_lesson_state(lesson_id: str):  # noqa: ANN202
        state = _FAKE_GRAPH_STATES.get(lesson_id)
        return dict(state) if state else None

    async def get_teaching_state(lesson_id: str):  # noqa: ANN202
        state = _FAKE_GRAPH_STATES.get(lesson_id)
        if state is None:
            return None
        return {
            "current_concept_id": state.get("current_concept"),
            "completed_concept_ids": list(state.get("completed_concepts") or []),
            "pending_concept_ids": list(state.get("pending_concepts") or []),
            "progress": state.get("progress", 0.0),
            "understanding_score": state.get("understanding_score", 0.0),
            "assessment_results": list(state.get("assessment_results") or []),
            "recommended_action": state.get("recommended_action"),
            "is_finished": state.get("status") in {"completed", "abandoned"},
        }

    async def finish_lesson(db, lesson_id, reason=None):  # noqa: ANN001, ANN202
        state = dict(_FAKE_GRAPH_STATES.get(lesson_id) or {})
        state.update(
            {
                "status": "completed",
                "progress": 1.0,
                "summary": "The lesson covered the planned concepts.",
                "current_concept": None,
            }
        )
        _FAKE_GRAPH_STATES[lesson_id] = state
        return state

    async def delete_student_memory(student_id: str):  # noqa: ANN202
        for lesson_id in list(_FAKE_GRAPH_STATES):
            if _FAKE_GRAPH_STATES[lesson_id].get("student_id") == student_id:
                _FAKE_GRAPH_STATES.pop(lesson_id, None)
        return {"deleted": True, "student_id": student_id}

    async def init_persistence() -> None:
        return None

    async def shutdown_persistence() -> None:
        return None

    module.start_lesson = start_lesson
    module.load_lesson_state = load_lesson_state
    module.get_teaching_state = get_teaching_state
    module.finish_lesson = finish_lesson
    module.delete_student_memory = delete_student_memory
    module.init_persistence = init_persistence
    module.shutdown_persistence = shutdown_persistence
    sys.modules["app.graph.service"] = module
    try:
        graph_package = importlib.import_module("app.graph")
        setattr(graph_package, "service", module)
    except ImportError:  # pragma: no cover - app.graph always ships with the app
        pass


def _install_fake_realtime_router() -> None:
    from fastapi import APIRouter

    module = types.ModuleType("app.realtime.router")
    module.__doc__ = "Temporary stand-in for app.realtime.router."
    router = APIRouter(tags=["realtime"])

    @router.get("/ws/_stub")
    async def _stub() -> dict:
        return {"status": "realtime-stub"}

    module.router = router
    sys.modules["app.realtime.router"] = module


if not _module_exists("app.graph.service"):
    _install_fake_graph_service()
if not _module_exists("app.realtime.router"):
    _install_fake_realtime_router()

# ---------------------------------------------------------------------------
# app / database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_database():
    """Give every test a fresh schema (cheap for a local SQLite file)."""
    from app.db.session import reset_models

    await reset_models()
    yield


@pytest.fixture
async def app_instance():
    from app.db.session import get_db, get_session_factory
    from app.main import app

    factory = get_session_factory()

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield app
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def client(app_instance):
    import httpx

    transport = httpx.ASGITransport(app=app_instance)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http_client:
        yield http_client


# ---------------------------------------------------------------------------
# identity fixtures
# ---------------------------------------------------------------------------


async def _create_student(email: str, display_name: str, password: str = "secret123") -> dict:
    from app.core.security import create_access_token, hash_password
    from app.db import repositories
    from app.db.session import session_scope

    async with session_scope() as db:
        user = await repositories.create_user(
            db,
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
        )
        student_id = user.id
    return {
        "id": student_id,
        "email": email,
        "password": password,
        "display_name": display_name,
        "token": create_access_token(student_id),
    }


@pytest.fixture
async def seeded_student() -> dict:
    return await _create_student("student.one@example.com", "Student One")


@pytest.fixture
async def other_student() -> dict:
    return await _create_student("student.two@example.com", "Student Two")


@pytest.fixture
def auth_headers():
    def _headers(student: dict) -> dict:
        return {"Authorization": f"Bearer {student['token']}"}

    return _headers


# ---------------------------------------------------------------------------
# data-building helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def make_lesson(client, auth_headers):
    async def _make(student: dict, **overrides) -> dict:
        payload = {
            "topic": "Introduction to Photosynthesis",
            "learning_level": "high_school",
            "duration_minutes": 20,
            "subject": "Biology",
        }
        payload.update(overrides)
        response = await client.post(
            "/api/v1/lessons", json=payload, headers=auth_headers(student)
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _make


@pytest.fixture
def append_turns():
    async def _append(lesson_id: str, turns: list) -> None:
        from app.lessons.transcripts import append_transcript_entry
        from app.db.session import session_scope

        async with session_scope() as db:
            for turn in turns:
                if isinstance(turn, str):
                    speaker, text = "tutor", turn
                else:
                    speaker, text = turn[0], turn[1]
                await append_transcript_entry(db, lesson_id, speaker, text)

    return _append


@pytest.fixture
def seed_board():
    """Insert board events (and optionally a snapshot) the way app.board would."""

    async def _seed(
        lesson_id: str,
        events: list[dict],
        *,
        with_snapshot: bool = False,
        snapshot_revision: int | None = None,
    ) -> list[dict]:
        from app.db.base import utcnow
        from app.db.models import Lesson, LessonBoardEvent, LessonBoardSnapshot
        from app.db.session import session_scope

        stored: list[dict] = []
        async with session_scope() as db:
            for index, event in enumerate(events, start=1):
                record = {
                    "event_id": event.get("event_id") or f"evt_test_{lesson_id[:6]}_{index}",
                    "type": event["type"],
                    "element_id": event.get("element_id"),
                    "payload": event.get("payload") or {},
                    "sequence": event.get("sequence", index),
                    "board_revision": event.get("board_revision", index),
                }
                db.add(
                    LessonBoardEvent(
                        lesson_id=lesson_id,
                        event_id=record["event_id"],
                        sequence=record["sequence"],
                        type=record["type"],
                        element_id=record["element_id"],
                        payload=record["payload"],
                        board_revision=record["board_revision"],
                        depends_on=event.get("depends_on") or [],
                        source=event.get("source") or "qwen_tool",
                        animate=event.get("animate", True),
                    )
                )
                stored.append(record)
            lesson = await db.get(Lesson, lesson_id)
            if lesson is not None:
                lesson.last_board_sequence = stored[-1]["sequence"] if stored else 0
                lesson.board_revision = stored[-1]["board_revision"] if stored else 0
            if with_snapshot:
                revision = (
                    snapshot_revision
                    if snapshot_revision is not None
                    else (stored[-1]["board_revision"] if stored else 0)
                )
                last_sequence = int(revision)
                included = [
                    event for event in stored if event["sequence"] <= last_sequence
                ]
                document = {
                    "session_id": lesson_id,
                    "revision": revision,
                    "last_sequence": last_sequence,
                    "elements": [
                        {
                            "element_id": event["element_id"],
                            "type": event["type"],
                            "level": 2,
                            "payload": event["payload"],
                            "position": event["payload"].get("position"),
                            "size": None,
                            "style": event["payload"].get("style"),
                            "color": event["payload"].get("color"),
                            "created_sequence": event["sequence"],
                            "updated_sequence": None,
                            "removed_at_sequence": None,
                            "animation": True,
                        }
                        for event in included
                        if event["element_id"]
                    ],
                    "created_at": utcnow().isoformat(),
                    "updated_at": utcnow().isoformat(),
                }
                db.add(
                    LessonBoardSnapshot(
                        lesson_id=lesson_id,
                        revision=revision,
                        last_sequence=last_sequence,
                        document=document,
                    )
                )
            await db.flush()
        return stored

    return _seed


@pytest.fixture
def wait_for_note(client, auth_headers):
    async def _wait(student: dict, note_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while time.monotonic() < deadline:
            response = await client.get(
                f"/api/v1/notes/{note_id}", headers=auth_headers(student)
            )
            assert response.status_code == 200, response.text
            last = response.json()
            if last["status"] != "pending":
                return last
            await asyncio.sleep(0.02)
        assert last is not None
        return last

    return _wait


@pytest.fixture
def count_rows():
    async def _count(model) -> int:
        from sqlalchemy import func, select
        from app.db.session import session_scope

        async with session_scope() as db:
            return int((await db.execute(select(func.count()).select_from(model))).scalar_one())

    return _count
