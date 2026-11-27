"""Tests for realtime automatic teaching startup and board event broadcasting."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.db.models import Lesson, User
from app.db.session import session_scope
from app.realtime.hub import (
    CLIENT_BOARD_CLEAR,
    CLIENT_BOARD_VIEWPORT_FULL,
    LessonRealtimeHub,
    LessonSocket,
)


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


class FakeQwen:
    def __init__(self) -> None:
        self.connected = False
        self.instructions: str | None = None
        self.tools: list[dict] | None = None
        self.prompt_texts: list[str] = []
        self.response_creates = 0
        self.commits = 0

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        self.connected = True
        self.instructions = instructions
        self.tools = tools

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def commit_audio(self) -> None:
        self.commits += 1

    async def create_response(self) -> None:
        self.response_creates += 1

    async def cancel_response(self) -> None:
        return None

    async def function_call_output(self, call_id: str, output: str) -> None:
        return None

    async def prompt_text(self, text: str) -> None:
        self.prompt_texts.append(text)
        self.response_creates += 1

    async def iterate(self):
        if False:
            yield {}
        return

    async def aclose(self) -> None:
        return None


async def _create_lesson(*, topic: str = "Photosynthesis", status: str = "live") -> str:
    async with session_scope() as db:
        user = await db.get(User, "student-1")
        if user is None:
            user = User(
                id="student-1",
                email="student-1@example.com",
                password_hash="x",
                display_name="Student 1",
            )
            db.add(user)
            await db.flush()
        lesson = Lesson(
            student_id=user.id,
            topic=topic,
            subject="Biology",
            learning_level="beginner",
            duration_minutes=20,
            status=status,
        )
        db.add(lesson)
        await db.flush()
        return lesson.id


@pytest.mark.asyncio
async def test_initial_teaching_starts_for_fresh_lesson() -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)

    detail = {
        "topic": "Photosynthesis",
        "subject": "Biology",
        "learning_level": "beginner",
        "plan": {"concepts": [{"title": "Light"}]},
        "lesson_state": {},
    }
    board_envelope = {"last_sequence": 0, "revision": 0}

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await hub._maybe_start_initial_teaching(socket, lesson, detail, board_envelope, 0)

    assert len(qwen.prompt_texts) == 1
    prompt = qwen.prompt_texts[0]
    assert "Start the live lesson now" in prompt
    assert "Photosynthesis" in prompt
    assert "lesson title as ONE short line" in prompt
    assert "board_write_on_board" in prompt
    assert "ONE short line per board_write_on_board call" in prompt
    assert socket.initial_teaching_started is True


@pytest.mark.asyncio
async def test_initial_teaching_duplicate_prevention() -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)

    detail = {"topic": "Photosynthesis", "subject": "Biology", "learning_level": "beginner", "plan": {}, "lesson_state": {}}
    board_envelope = {"last_sequence": 0}

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await hub._maybe_start_initial_teaching(socket, lesson, detail, board_envelope, 0)
        await hub._maybe_start_initial_teaching(socket, lesson, detail, board_envelope, 0)

    assert len(qwen.prompt_texts) == 1


@pytest.mark.asyncio
async def test_initial_teaching_resume_does_not_restart() -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)

    detail = {
        "topic": "Photosynthesis",
        "subject": "Biology",
        "learning_level": "beginner",
        "plan": {},
        "lesson_state": {"current_concept": "chlorophyll"},
    }
    board_envelope = {"last_sequence": 3, "revision": 3}

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await hub._maybe_start_initial_teaching(socket, lesson, detail, board_envelope, 2)

    assert len(qwen.prompt_texts) == 1
    prompt = qwen.prompt_texts[0]
    assert "Resume this live lesson" in prompt
    assert "Do not repeat already completed content" in prompt
    assert "Start the live lesson now" not in prompt


@pytest.mark.asyncio
async def test_completed_lesson_does_not_auto_start() -> None:
    lesson_id = await _create_lesson(status="completed")
    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)

    detail = {"topic": "Photosynthesis", "subject": "Biology", "learning_level": "beginner", "plan": {}, "lesson_state": {}}

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await hub._maybe_start_initial_teaching(socket, lesson, detail, {"last_sequence": 0}, 0)

    assert qwen.prompt_texts == []


@pytest.mark.asyncio
async def test_visible_board_full_wipes_once_and_nudges_tutor() -> None:
    lesson_id = await _create_lesson()
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        lesson.last_board_sequence = 4
        await db.commit()

    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(
        websocket=ws,
        lesson_id=lesson_id,
        student_id="student-1",
        lease_id="lease-1",
        qwen=qwen,
    )
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)
    report = json.dumps(
        {
            "type": CLIENT_BOARD_VIEWPORT_FULL,
            "sequence": 4,
            "coverage": 0.91,
            "scale": 2.0,
            "visible_rect": {"x": 0, "y": 0, "w": 1, "h": 1},
            "offset": {"x": 0, "y": 0},
        }
    )

    await hub._handle_client_text(socket, lesson_id, report)
    clears = [
        m
        for m in ws.sent
        if m.get("type") == "board.event" and m["event"].get("type") == "clear"
    ]
    assert len(clears) == 1
    assert len(qwen.prompt_texts) == 1
    assert "visible board was full" in qwen.prompt_texts[0]

    # Duplicate and stale reports must not wipe the freshly recycled board.
    await hub._handle_client_text(socket, lesson_id, report)
    stale = json.loads(report)
    stale.update(sequence=3, coverage=0.95)
    await hub._handle_client_text(socket, lesson_id, json.dumps(stale))
    later_clears = [
        m
        for m in ws.sent
        if m.get("type") == "board.event" and m["event"].get("type") == "clear"
    ]
    assert len(later_clears) == 1
    assert len(qwen.prompt_texts) == 1


@pytest.mark.asyncio
async def test_manual_board_clear_wipes_once_without_nudging_tutor() -> None:
    lesson_id = await _create_lesson()
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        lesson.last_board_sequence = 4
        await db.commit()

    hub = LessonRealtimeHub()
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(
        websocket=ws,
        lesson_id=lesson_id,
        student_id="student-1",
        lease_id="lease-1",
        qwen=qwen,
    )
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    request = json.dumps({"type": CLIENT_BOARD_CLEAR, "sequence": 4})
    await hub._handle_client_text(socket, lesson_id, request)
    clears = [
        m
        for m in ws.sent
        if m.get("type") == "board.event" and m["event"].get("type") == "clear"
    ]
    assert len(clears) == 1
    assert qwen.prompt_texts == []

    # Duplicate and stale requests cannot wipe the freshly recycled board.
    await hub._handle_client_text(socket, lesson_id, request)
    await hub._handle_client_text(
        socket,
        lesson_id,
        json.dumps({"type": CLIENT_BOARD_CLEAR, "sequence": 3}),
    )
    later_clears = [
        m
        for m in ws.sent
        if m.get("type") == "board.event" and m["event"].get("type") == "clear"
    ]
    assert len(later_clears) == 1


@pytest.mark.asyncio
async def test_board_event_broadcast_to_live_socket() -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    ws = FakeWs()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1")
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    event = {
        "event_id": "evt_1",
        "sequence": 1,
        "type": "draw_plan",
        "element_id": "el_1",
        "payload": {"protocol": "clasrum.drawing", "version": 1, "elements": []},
    }
    await hub.publish_board_event(lesson_id, event)

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "board.event"
    assert ws.sent[0]["event"]["sequence"] == 1
    assert socket.last_board_event_at > 0


@pytest.mark.asyncio
async def test_board_intent_tool_call_persists_and_broadcasts_draw_plan(monkeypatch) -> None:
    from app.board.service import BoardService

    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    board = BoardService()
    hub.set_board_service(board)  # also wires publisher
    ws = FakeWs()
    qwen = FakeQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async def fake_director(_context):
        return {
            "protocol": "clasrum.drawing",
            "version": 1,
            "title": "Photosynthesis",
            "elements": [
                {
                    "id": "e1",
                    "kind": "text",
                    "geometry": {"text": "Photosynthesis", "position": {"x": 0.1, "y": 0.1}},
                    "stage": 1,
                    "duration_ms": 300,
                    "delay_ms": 0,
                },
                {
                    "id": "e2",
                    "kind": "rectangle",
                    "geometry": {"position": {"x": 0.2, "y": 0.2}, "size": {"w": 0.3, "h": 0.2}},
                    "stage": 1,
                    "duration_ms": 400,
                    "delay_ms": 0,
                },
            ],
            "stages": [{"stage": 1, "description": "intro", "element_ids": ["e1", "e2"], "pause_ms": 0}],
        }

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)

    await hub._handle_qwen_tool_call(
        socket,
        lesson_id,
        {"call_id": "call_1", "name": "board_intent", "arguments": {"intent": "Draw lesson heading and a simple diagram", "focus": "new_content"}},
    )

    board_msgs = [m for m in ws.sent if m.get("type") == "board.event"]
    assert board_msgs, f"no board.event broadcast: {ws.sent}"
    evt = board_msgs[0]["event"]
    assert evt["type"] == "draw_plan"
    assert evt["payload"]["title"] == "Photosynthesis"

    async with session_scope() as db:
        events = await BoardService().get_events(db, lesson_id)
        assert any(e.type == "draw_plan" for e in events)
