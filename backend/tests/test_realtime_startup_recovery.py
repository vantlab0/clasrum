"""Regression tests for realtime startup recovery and first-board reliability."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.board.service import BoardService
from app.db.models import Lesson, User
from app.db.session import session_scope
from app.realtime.hub import LessonRealtimeHub, LessonSocket


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


class ScriptedQwen:
    def __init__(self) -> None:
        self.connected = False
        self.instructions: str | None = None
        self.tools: list[dict] | None = None
        self.prompt_texts: list[str] = []
        self.function_outputs: list[tuple[str, str]] = []
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        self.connected = True
        self.instructions = instructions
        self.tools = tools

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def commit_audio(self) -> None:
        return None

    async def create_response(self) -> None:
        return None

    async def cancel_response(self) -> None:
        return None

    async def function_call_output(self, call_id: str, output: str) -> None:
        self.function_outputs.append((call_id, output))

    async def prompt_text(self, text: str) -> None:
        self.prompt_texts.append(text)

    async def iterate(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def aclose(self) -> None:
        await self._queue.put(None)

    def push(self, event: dict) -> None:
        self._queue.put_nowait(event)

    async def close_stream(self) -> None:
        await self._queue.put(None)


async def _create_lesson(*, topic: str = "Fractions", status: str = "live") -> str:
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
            subject="Math",
            learning_level="beginner",
            duration_minutes=20,
            status=status,
        )
        db.add(lesson)
        await db.flush()
        return lesson.id


def _valid_plan(title: str = "Fractions") -> dict:
    return {
        "protocol": "clasrum.drawing",
        "version": 1,
        "title": title,
        "elements": [
            {"id": "h1", "kind": "text", "geometry": {"text": title, "position": {"x": 0.08, "y": 0.08}, "align": "left", "font_scale": 1.2}, "style": {"stroke_color": "primary", "stroke_width": 1.0, "stroke_opacity": 1.0}, "layer": 1, "stage": 1, "duration_ms": 400, "delay_ms": 0},
            {"id": "intro", "kind": "text", "geometry": {"text": f"Introduction to {title}", "position": {"x": 0.08, "y": 0.14}, "align": "left", "font_scale": 0.8}, "style": {"stroke_color": "secondary", "stroke_width": 0.8, "stroke_opacity": 0.9}, "layer": 1, "stage": 1, "duration_ms": 350, "delay_ms": 50},
            {"id": "c1", "kind": "text", "geometry": {"text": "What is a fraction?", "position": {"x": 0.08, "y": 0.24}, "align": "left", "font_scale": 1.0}, "style": {"stroke_color": "yellow", "stroke_width": 1.0, "stroke_opacity": 1.0}, "layer": 1, "stage": 2, "duration_ms": 350, "delay_ms": 0},
            {"id": "def", "kind": "text", "geometry": {"text": "A fraction shows part of a whole.", "position": {"x": 0.08, "y": 0.31}, "align": "left", "font_scale": 0.75}, "style": {"stroke_color": "primary", "stroke_width": 0.8, "stroke_opacity": 0.95}, "layer": 1, "stage": 2, "duration_ms": 500, "delay_ms": 60},
            {"id": "box", "kind": "rectangle", "geometry": {"position": {"x": 0.58, "y": 0.28}, "size": {"w": 0.28, "h": 0.2}, "corner_radius": 0.01}, "style": {"stroke_color": "blue", "stroke_width": 1.4, "stroke_opacity": 0.95, "fill_color": "#4A90D9", "fill_opacity": 0.08}, "layer": 2, "stage": 3, "duration_ms": 500, "delay_ms": 0},
        ],
        "stages": [
            {"stage": 1, "description": "heading", "element_ids": ["h1", "intro"], "pause_ms": 100},
            {"stage": 2, "description": "concept", "element_ids": ["c1", "def"], "pause_ms": 100},
            {"stage": 3, "description": "diagram", "element_ids": ["box"], "pause_ms": 0},
        ],
    }


@pytest.mark.asyncio
async def test_startup_recovers_when_first_response_has_no_board_tool_call(monkeypatch) -> None:
    """Regression: response without board_intent triggers controlled recovery and board renders."""
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    board = BoardService()
    hub.set_board_service(board)
    ws = FakeWs()
    qwen = ScriptedQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async def fake_director(_context):
        return _valid_plan("Fractions")

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)

    # Initial prompt
    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await hub._maybe_start_initial_teaching(
            socket, lesson,
            {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}, "lesson_state": {}},
            {"last_sequence": 0}, 0,
        )
    assert len(qwen.prompt_texts) == 1

    loop_task = asyncio.create_task(hub._qwen_loop(socket, lesson_id))

    # 1) response.created, audio+transcript, but no board tool call
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.audio.delta", "response_id": "r1", "delta": "AAAA", "sequence": 0})
    qwen.push({"type": "response.audio_transcript.done", "response_id": "r1", "speaker": "tutor", "transcript": "Welcome to fractions."})
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})

    # give hub time to process and send recovery nudge
    for _ in range(50):
        if len(qwen.prompt_texts) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(qwen.prompt_texts) >= 2, f"expected recovery nudge, got {qwen.prompt_texts}"
    recovery_prompt = qwen.prompt_texts[-1].lower()
    assert "board_intent" in recovery_prompt

    # 2) model emits board_intent
    qwen.push({"type": "response.created", "response": {"id": "r2", "status": "in_progress"}})
    qwen.push({
        "type": "response.function_call_arguments.done",
        "response_id": "r2",
        "call_id": "call_1",
        "name": "board_intent",
        "arguments": json.dumps({"intent": "Draw lesson heading and first concept visual", "focus": "new_content"}),
    })
    qwen.push({"type": "response.done", "response": {"id": "r2", "status": "completed"}})

    for _ in range(50):
        if any(m.get("type") == "board.event" for m in ws.sent):
            break
        await asyncio.sleep(0.02)

    await qwen.close_stream()
    try:
        await asyncio.wait_for(loop_task, timeout=2)
    except asyncio.TimeoutError:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    board_msgs = [m for m in ws.sent if m.get("type") == "board.event"]
    assert board_msgs, f"no board.event: {[m.get('type') for m in ws.sent]}"
    evt = board_msgs[0]["event"]
    assert evt["type"] == "draw_plan"

    async with session_scope() as db:
        events = await BoardService().get_events(db, lesson_id)
        assert any(e.type == "draw_plan" for e in events)

    assert socket.startup_has_board_event is True


@pytest.mark.asyncio
async def test_reconnect_after_successful_start_does_not_duplicate(monkeypatch) -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    board = BoardService()
    hub.set_board_service(board)

    async def fake_director(_context):
        return _valid_plan("Fractions")

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)

    # First socket: successful startup with board event.
    ws1 = FakeWs()
    qwen1 = ScriptedQwen()
    s1 = LessonSocket(websocket=ws1, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen1)
    async with hub._lock:
        hub._sockets[lesson_id] = s1
        hub._all.setdefault(lesson_id, set()).add(s1)

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        await hub._maybe_start_initial_teaching(s1, lesson, {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}, "lesson_state": {}}, {"last_sequence": 0}, 0)
    # Simulate board_intent producing event
    await hub._handle_qwen_tool_call(s1, lesson_id, {"call_id": "c1", "name": "board_intent", "arguments": {"intent": "heading", "focus": "new_content"}})
    assert any(m.get("type") == "board.event" for m in ws1.sent)

    # Second socket reconnects: should not restart from beginning.
    ws2 = FakeWs()
    qwen2 = ScriptedQwen()
    s2 = LessonSocket(websocket=ws2, lesson_id=lesson_id, student_id="student-1", lease_id="lease-2", qwen=qwen2)
    async with hub._lock:
        hub._sockets[lesson_id] = s2
        hub._all.setdefault(lesson_id, set()).add(s2)

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        await hub._maybe_start_initial_teaching(s2, lesson, {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}, "lesson_state": {}}, {"last_sequence": 1}, 0)

    # Should not send a fresh "Start the live lesson now" prompt; resume or skip.
    for p in qwen2.prompt_texts:
        assert "Start the live lesson now" not in p


@pytest.mark.asyncio
async def test_failed_startup_can_retry(monkeypatch) -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    board = BoardService()
    hub.set_board_service(board)

    calls = {"n": 0}

    async def flaky_director(_context):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timeout")
        return _valid_plan("Fractions")

    monkeypatch.setattr("app.board.intent_handler.call_board_director", flaky_director)

    ws = FakeWs()
    qwen = ScriptedQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        await hub._maybe_start_initial_teaching(socket, lesson, {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}, "lesson_state": {}}, {"last_sequence": 0}, 0)

    # First attempt fails (director timeout -> tool returns ok=false, hub marks recovery).
    await hub._handle_qwen_tool_call(socket, lesson_id, {"call_id": "c1", "name": "board_intent", "arguments": {"intent": "heading", "focus": "new_content"}})
    assert socket.startup_has_board_event is False

    # Second attempt succeeds.
    await hub._handle_qwen_tool_call(socket, lesson_id, {"call_id": "c2", "name": "board_intent", "arguments": {"intent": "heading", "focus": "new_content"}})
    assert socket.startup_has_board_event is True
    assert any(m.get("type") == "board.event" for m in ws.sent)


@pytest.mark.asyncio
async def test_board_director_timeout_reports_failure_and_allows_recovery(monkeypatch) -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    hub.set_board_service(BoardService())
    ws = FakeWs()
    qwen = ScriptedQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async def timeout_director(_context):
        raise TimeoutError("board director timeout")

    monkeypatch.setattr("app.board.intent_handler.call_board_director", timeout_director)

    await hub._handle_qwen_tool_call(socket, lesson_id, {"call_id": "c1", "name": "board_intent", "arguments": {"intent": "heading", "focus": "new_content"}})
    assert socket.startup_has_board_event is False
    assert socket.last_board_director_error is not None
    # should send a startup status/error info, not crash
    assert any(m.get("type") in {"startup.status", "error"} for m in ws.sent)


@pytest.mark.asyncio
async def test_invalid_drawing_plan_reports_failure(monkeypatch) -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    hub.set_board_service(BoardService())
    ws = FakeWs()
    qwen = ScriptedQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async def bad_director(_context):
        return {"protocol": "clasrum.drawing", "version": 1, "elements": "bad"}

    monkeypatch.setattr("app.board.intent_handler.call_board_director", bad_director)

    await hub._handle_qwen_tool_call(socket, lesson_id, {"call_id": "c1", "name": "board_intent", "arguments": {"intent": "heading", "focus": "new_content"}})
    assert socket.startup_has_board_event is False
    assert socket.last_board_director_error is not None


@pytest.mark.asyncio
async def test_function_call_execution_failure_is_controlled(monkeypatch) -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    hub.set_board_service(BoardService())
    ws = FakeWs()
    qwen = ScriptedQwen()
    socket = LessonSocket(websocket=ws, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen)
    async with hub._lock:
        hub._sockets[lesson_id] = socket
        hub._all.setdefault(lesson_id, set()).add(socket)

    async def boom(*args, **kwargs):
        raise RuntimeError("db boom")

    monkeypatch.setattr("app.board.registry.ToolRegistry.execute_structured", boom)

    await hub._handle_qwen_tool_call(socket, lesson_id, {"call_id": "c1", "name": "board_intent", "arguments": {"intent": "heading"}})
    # No crash, failure recorded
    assert socket.startup_has_board_event is False
    assert qwen.function_outputs, "tool output should be sent back to qwen"


@pytest.mark.asyncio
async def test_concurrent_reconnect_attempts_do_not_duplicate_initial_prompt() -> None:
    lesson_id = await _create_lesson()
    hub = LessonRealtimeHub()
    detail = {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}, "lesson_state": {}}
    board_envelope = {"last_sequence": 0}

    ws1, qwen1 = FakeWs(), ScriptedQwen()
    ws2, qwen2 = FakeWs(), ScriptedQwen()
    s1 = LessonSocket(websocket=ws1, lesson_id=lesson_id, student_id="student-1", lease_id="lease-1", qwen=qwen1)
    s2 = LessonSocket(websocket=ws2, lesson_id=lesson_id, student_id="student-1", lease_id="lease-2", qwen=qwen2)
    async with hub._lock:
        hub._sockets[lesson_id] = s2
        hub._all.setdefault(lesson_id, set()).update({s1, s2})

    async with session_scope() as db:
        lesson = await db.get(Lesson, lesson_id)
        assert lesson is not None
        await asyncio.gather(
            hub._maybe_start_initial_teaching(s1, lesson, detail, board_envelope, 0),
            hub._maybe_start_initial_teaching(s2, lesson, detail, board_envelope, 0),
        )

    total = len(qwen1.prompt_texts) + len(qwen2.prompt_texts)
    assert total == 1, f"expected exactly 1 initial prompt, got {total}: {qwen1.prompt_texts + qwen2.prompt_texts}"
