"""Regression tests: continuous teaching with explicit wait-for-student pauses."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.realtime.hub import LessonRealtimeHub, LessonSocket, _teacher_instructions


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class FakeQwen:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.create_response_count = 0
        self.function_outputs: list[tuple[str, str]] = []
        self.instructions = ""

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        self.instructions = instructions

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def commit_audio(self) -> None:
        return None

    async def create_response(self) -> None:
        self.create_response_count += 1

    async def cancel_response(self) -> None:
        return None

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = True
    ) -> None:
        self.function_outputs.append((call_id, output, continue_response))

    async def prompt_text(self, text: str) -> None:
        return None

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
    assert socket.waiting_for_student is False
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
    assert qwen.function_outputs
    call_id, output, continue_response = qwen.function_outputs[0]
    assert call_id == "call_1"
    assert json.loads(output)["ok"] is True
    assert continue_response is False  # must stop and listen, not continue talking


def test_instructions_advertise_continuous_teaching() -> None:
    instructions = _teacher_instructions(
        {"topic": "Fractions", "subject": "Math", "learning_level": "beginner", "plan": {}}
    )
    assert "KEEP TEACHING AUTOMATICALLY" in instructions
    assert "wait_for_student" in instructions
