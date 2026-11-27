"""Regression tests: continuous teaching with explicit wait-for-student pauses."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.realtime.hub import (
    CLIENT_AUDIO_START,
    CLIENT_AUDIO_STOP,
    MSG_MIC_LOCK,
    MSG_MIC_UNLOCK,
    LessonRealtimeHub,
    LessonSocket,
    _teacher_instructions,
)


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class FakeQwen:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.create_response_count = 0
        self.clear_audio_count = 0
        self.function_outputs: list[tuple[str, str]] = []
        self.prompt_texts: list[str] = []
        self.instructions = ""

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        self.instructions = instructions

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def commit_audio(self) -> None:
        return None

    async def clear_audio(self) -> None:
        self.clear_audio_count += 1

    async def create_response(self) -> None:
        self.create_response_count += 1

    async def cancel_response(self) -> None:
        return None

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = True
    ) -> None:
        self.function_outputs.append((call_id, output, continue_response))

    async def prompt_text(self, text: str) -> None:
        self.prompt_texts.append(text)

    async def iterate(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event

    async def aclose(self) -> None:
        await self.queue.put(None)

    def push(self, event: dict) -> None:
        self.queue.put_nowait(event)


def _socket(qwen: FakeQwen) -> LessonSocket:
    return LessonSocket(
        websocket=FakeWs(),
        lesson_id="lesson-1",
        student_id="student-1",
        lease_id="lease-1",
        qwen=qwen,
        startup_has_board_event=True,
        startup_has_visual_event=True,
    )


@pytest.mark.asyncio
async def test_completed_teaching_response_auto_continues() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.audio.delta", "response_id": "r1", "delta": "AAAA"})
    qwen.push({"type": "response.audio_transcript.done", "response_id": "r1", "speaker": "tutor", "transcript": "Photosynthesis makes food."})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)
    assert qwen.create_response_count == 1
    assert socket.auto_continue_count == 1


@pytest.mark.asyncio
async def test_incomplete_teaching_response_also_auto_continues() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.audio.delta", "response_id": "r1", "delta": "AAAA"})
    qwen.push({"type": "response.audio_transcript.done", "response_id": "r1", "speaker": "tutor", "transcript": "Keep going."})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "incomplete"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)
    assert qwen.create_response_count == 1
    assert socket.auto_continue_count == 1


@pytest.mark.asyncio
async def test_title_only_startup_requests_diagram_before_auto_continue() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    # The title was written, but the concept diagram is still missing.
    socket.startup_has_visual_event = False
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.audio_transcript.done", "response_id": "r1", "speaker": "tutor", "transcript": "Photosynthesis."})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert qwen.create_response_count == 0
    assert qwen.prompt_texts
    assert "create_board_visual" in qwen.prompt_texts[-1]


@pytest.mark.asyncio
async def test_wait_for_student_pauses_auto_continuation() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    socket.waiting_for_student = True
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.audio.delta", "response_id": "r1", "delta": "AAAA"})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_tool_response_does_not_double_continue() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "response.created", "response": {"id": "r1", "status": "in_progress"}})
    qwen.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "unused", "name": "board_write_on_board", "arguments": "{}"})
    qwen.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_student_speech_started_clears_wait_and_notifies_client() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    socket.waiting_for_student = True
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 100})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)
    # Semantic VAD is disabled; legacy speech telemetry must not end the pause
    # or grant microphone access.
    assert socket.waiting_for_student is True
    assert socket.mic_unlocked is False
    assert any(m.get("type") == "user.speech_started" for m in socket.websocket.sent)


@pytest.mark.asyncio
async def test_wait_for_student_tool_call_is_handled_locally() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    await hub._handle_qwen_tool_call(
        socket,
        "lesson-1",
        {
            "call_id": "call_1",
            "name": "wait_for_student",
            "arguments": json.dumps({"reason": "Need the answer"}),
            "response_id": "r1",
        },
    )
    assert socket.waiting_for_student is True
    assert socket.mic_unlocked is True
    assert socket.mic_request_id == "call_1"
    assert qwen.function_outputs
    call_id, output, continue_response = qwen.function_outputs[0]
    assert call_id == "call_1"
    assert json.loads(output)["ok"] is True
    assert json.loads(output)["mic_unlocked"] is True
    assert continue_response is False  # must stop and listen, not continue talking
    unlocks = [m for m in socket.websocket.sent if m.get("type") == MSG_MIC_UNLOCK]
    assert len(unlocks) == 1
    assert unlocks[0]["request_id"] == "call_1"
    assert unlocks[0]["reason"] == "Need the answer"
    assert unlocks[0]["requires_answer"] is True
    assert unlocks[0]["turn_detection"] == "manual"


@pytest.mark.asyncio
async def test_replayed_wait_for_student_does_not_unlock_twice() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    event = {
        "call_id": "call_1",
        "name": "wait_for_student",
        "arguments": json.dumps({"reason": "Need the answer"}),
        "response_id": "r1",
    }
    await hub._handle_qwen_tool_call(socket, "lesson-1", event)
    await hub._handle_qwen_tool_call(socket, "lesson-1", event)
    unlocks = [m for m in socket.websocket.sent if m.get("type") == MSG_MIC_UNLOCK]
    assert len(unlocks) == 1
    assert len(qwen.function_outputs) == 2
    assert qwen.function_outputs[-1][2] is False


@pytest.mark.asyncio
async def test_audio_start_cannot_clear_wait_and_audio_stop_submits_answer() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    await hub._handle_qwen_tool_call(
        socket,
        "lesson-1",
        {
            "call_id": "call_1",
            "name": "wait_for_student",
            "arguments": "{}",
            "response_id": "r1",
        },
    )
    await hub._handle_client_text(
        socket,
        "lesson-1",
        json.dumps({
            "type": CLIENT_AUDIO_START,
            "reason": "answer",
            "request_id": "call_1",
        }),
    )
    assert socket.audio_turn_active is True
    assert socket.waiting_for_student is True
    await hub._handle_client_text(
        socket,
        "lesson-1",
        json.dumps({
            "type": CLIENT_AUDIO_STOP,
            "submitted": True,
            "request_id": "call_1",
        }),
    )
    assert socket.waiting_for_student is False
    assert socket.mic_unlocked is False
    assert qwen.create_response_count == 1
    assert any(m.get("type") == MSG_MIC_LOCK for m in socket.websocket.sent)


@pytest.mark.asyncio
async def test_locked_audio_start_is_ignored_and_cancelled_answer_keeps_wait() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    await hub._handle_client_text(
        socket,
        "lesson-1",
        json.dumps({"type": CLIENT_AUDIO_START, "reason": "answer"}),
    )
    assert socket.audio_turn_active is False
    await hub._handle_qwen_tool_call(
        socket,
        "lesson-1",
        {
            "call_id": "call_2",
            "name": "wait_for_student",
            "arguments": "{}",
            "response_id": "r1",
        },
    )
    await hub._handle_client_text(
        socket,
        "lesson-1",
        json.dumps({
            "type": CLIENT_AUDIO_STOP,
            "submitted": False,
            "request_id": "call_2",
        }),
    )
    assert socket.waiting_for_student is True
    assert socket.mic_unlocked is True  # retry remains possible
    assert qwen.clear_audio_count == 1
    assert qwen.create_response_count == 0
    assert not any(m.get("type") == MSG_MIC_LOCK for m in socket.websocket.sent)


def test_instructions_advertise_continuous_teaching() -> None:
    instructions = _teacher_instructions(
        {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}}
    )
    assert "KEEP TEACHING AUTOMATICALLY" in instructions
    assert "wait_for_student" in instructions
    assert "unlocks the student's microphone" in instructions
    assert "Rhetorical" in instructions or "rhetorical" in instructions
