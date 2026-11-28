"""Regression tests for audio/tool gating and central response requests.

These tests use in-process fake transports (the same convention as the other
realtime tests); no production code is replaced except the out-of-process board
director/registry boundary via pytest monkeypatch.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.board.registry import ToolRegistry
from app.realtime.hub import (
    MSG_AUDIO_DELTA,
    MSG_AUDIO_DONE,
    MSG_AUDIO_TRUNCATED,
    MSG_TRANSCRIPT_ENTRY,
    LessonRealtimeHub,
    LessonSocket,
)


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    def of_type(self, msg_type: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("type") == msg_type]


class FakeQwen:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.create_response_count = 0
        self.function_outputs: list[tuple[str, str, bool]] = []
        self.prompt_texts: list[str] = []

    async def connect(self, instructions: str, tools: list[dict]) -> None:
        return None

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def commit_audio(self) -> None:
        return None

    async def clear_audio(self) -> None:
        return None

    async def create_response(self) -> None:
        self.create_response_count += 1

    async def cancel_response(self) -> None:
        return None

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = False
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

    def push(self, event: dict[str, Any]) -> None:
        self.queue.put_nowait(event)


def _socket(qwen: FakeQwen) -> LessonSocket:
    return LessonSocket(
        websocket=FakeWs(),
        lesson_id="lesson-1",
        student_id="student-1",
        lease_id="lease-1",
        qwen=qwen,  # type: ignore[arg-type]
        startup_has_board_event=True,
        startup_has_visual_event=True,
    )


def _created(response_id: str = "r1") -> dict[str, Any]:
    return {"type": "response.created", "response": {"id": response_id}}


def _delta(data: str, sequence: int) -> dict[str, Any]:
    return {
        "type": "response.audio.delta",
        "response_id": "r1",
        "delta": data,
        "sequence": sequence,
    }


def _call(call_id: str = "call_1", name: str = "board_write_on_board") -> dict[str, Any]:
    return {
        "type": "response.function_call_arguments.done",
        "response_id": "r1",
        "call_id": call_id,
        "name": name,
        "arguments": "{}",
    }


def _done(status: str = "completed") -> dict[str, Any]:
    return {"type": "response.done", "response": {"id": "r1", "status": status}}


async def _wait_for(predicate, timeout: float = 1.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.asyncio
async def test_audio_is_not_blocked_by_slow_tool_and_flushed_in_order(monkeypatch) -> None:
    """The event loop keeps consuming events; buffered deltas flush after the tool."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def slow_execute(self, db, lesson_id, name, arguments, *, source=None):
        tool_started.set()
        await release_tool.wait()
        return {"ok": True, "sequence": 1}

    monkeypatch.setattr(ToolRegistry, "execute_structured", slow_execute)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("B", 1))
    qwen.push(_call())
    qwen.push(_delta("C", 2))
    qwen.push(
        {
            "type": "response.audio_transcript.done",
            "response_id": "r1",
            "speaker": "tutor",
            "transcript": "Still processing events.",
        }
    )
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())

    await asyncio.wait_for(tool_started.wait(), timeout=1.5)
    # A later event must be processed while the tool is still running.
    await _wait_for(lambda: bool(socket.websocket.of_type(MSG_TRANSCRIPT_ENTRY)))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []

    release_tool.set()
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    deltas = socket.websocket.of_type(MSG_AUDIO_DELTA)
    assert [delta["data"] for delta in deltas] == ["A", "B", "C"]
    assert [delta["sequence"] for delta in deltas] == [0, 1, 2]
    done = socket.websocket.of_type(MSG_AUDIO_DONE)
    assert len(done) == 1
    assert socket.websocket.sent.index(done[0]) > socket.websocket.sent.index(deltas[-1])
    assert qwen.function_outputs and json.loads(qwen.function_outputs[0][1])["ok"] is True


@pytest.mark.asyncio
async def test_failed_tool_discards_buffered_speech(monkeypatch) -> None:
    """No audio.delta/audio.done may be emitted when a tool fails."""

    async def failing_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": False, "error": "board_unavailable"}

    monkeypatch.setattr(ToolRegistry, "execute_structured", failing_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Confirmation that must not be spoken", 0))
    qwen.push(_call())
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.function_outputs
    assert json.loads(qwen.function_outputs[0][1])["ok"] is False
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_replayed_call_resends_cached_result_without_second_response(monkeypatch) -> None:
    """A replayed (response_id, call_id) must not request a second response."""

    async def ok_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": True, "sequence": 1}

    monkeypatch.setattr(ToolRegistry, "execute_structured", ok_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_call())
    qwen.push(_done())
    await _wait_for(lambda: qwen.create_response_count == 1)
    # Replay after the original tool already settled and was acknowledged.
    qwen.push(_call())
    await _wait_for(lambda: len(qwen.function_outputs) == 2)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert len(qwen.function_outputs) == 2
    assert json.loads(qwen.function_outputs[0][1]) == json.loads(qwen.function_outputs[1][1])
    assert all(output[2] is False for output in qwen.function_outputs)
    assert qwen.create_response_count == 1


@pytest.mark.asyncio
async def test_auto_continue_runs_despite_busy_director_within_five_seconds() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    socket.last_board_director_started_at = time.monotonic()
    socket.last_board_director_finished_at = 0.0

    started = time.monotonic()
    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(
        {
            "type": "response.audio_transcript.done",
            "response_id": "r1",
            "speaker": "tutor",
            "transcript": "Next step.",
        }
    )
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=5)

    elapsed = time.monotonic() - started
    assert qwen.create_response_count == 1
    assert elapsed <= 5.0


@pytest.mark.asyncio
async def test_wait_for_student_still_pauses_continuation() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_call(name="wait_for_student"))
    qwen.push(_delta("Listen now.", 0))
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.waiting_for_student is True
    assert qwen.create_response_count == 0
    # wait_for_student is acknowledged locally, so its question speech is
    # released; only the *next* teaching response is paused.
    assert len(socket.websocket.of_type(MSG_AUDIO_DELTA)) == 1


@pytest.mark.asyncio
async def test_cancelled_response_discards_buffered_audio(monkeypatch) -> None:
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def slow_execute(self, db, lesson_id, name, arguments, *, source=None):
        tool_started.set()
        await release_tool.wait()
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", slow_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_call())
    qwen.push(_delta("Must not play", 0))
    qwen.push({"type": "response.cancel", "response_id": "r1"})
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done(status="cancelled"))
    await asyncio.wait_for(tool_started.wait(), timeout=1.5)
    release_tool.set()
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)


@pytest.mark.asyncio
async def test_superseded_socket_discards_buffered_audio() -> None:
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    await hub._handle_audio_delta(
        socket,
        "lesson-1",
        {"type": "response.audio.delta", "response_id": "r1", "delta": "old", "sequence": 0},
        "r1",
    )
    assert socket.response_gates["r1"].buffer

    await hub._cancel_pending_work(socket, reason="superseded")

    assert socket.response_gates["r1"].discarded is True
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)


@pytest.mark.asyncio
async def test_adapter_does_not_implicitly_create_responses() -> None:
    """function_call_output/prompt_text only send items; the hub owns responses."""
    from app.realtime.qwen_adapter import QwenRealtimeSession

    session = QwenRealtimeSession(api_key="test-key")
    sent: list[dict[str, Any]] = []

    class _RecordingWs:
        async def send(self, payload: str) -> None:
            sent.append(json.loads(payload))

    session._ws = _RecordingWs()  # type: ignore[assignment]
    await session.function_call_output("call_1", "{}")
    await session.prompt_text("System instruction: continue.")
    await session.function_call_output("call_2", "{}", continue_response=True)

    assert [message["type"] for message in sent] == [
        "conversation.item.create",
        "conversation.item.create",
        "conversation.item.create",
        "response.create",
    ]


@pytest.mark.asyncio
async def test_audio_deltas_are_deduped_sorted_and_gap_recovered() -> None:
    """Out-of-order deltas are released in sequence order; gaps trigger recovery."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("D", 3))  # source-side gap: 1 and 2 not seen yet
    qwen.push(_delta("B", 1))
    qwen.push(_delta("duplicate", 1))
    qwen.push(_delta("C", 2))
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    deltas = socket.websocket.of_type(MSG_AUDIO_DELTA)
    assert [delta["data"] for delta in deltas] == ["A", "B", "C", "D"]
    assert [delta["sequence"] for delta in deltas] == [0, 1, 2, 3]
    assert socket.audio_gap_count == 1
    assert qwen.create_response_count == 1  # guarded gap recovery response


@pytest.mark.asyncio
async def test_concurrent_forced_response_requests_are_deduplicated() -> None:
    """A create in flight is a guard even before response.created arrives."""

    class SlowCreateQwen(FakeQwen):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def create_response(self) -> None:
            self.started.set()
            await self.release.wait()
            self.create_response_count += 1

    hub = LessonRealtimeHub()
    qwen = SlowCreateQwen()
    socket = _socket(qwen)
    tasks = [
        asyncio.create_task(hub._request_response(socket, reason="first", force=True)),
        asyncio.create_task(hub._request_response(socket, reason="second", force=True)),
    ]
    await asyncio.wait_for(qwen.started.wait(), timeout=1.5)
    qwen.release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1.5)

    assert qwen.create_response_count == 1
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_one_failed_tool_cancels_continuation_even_if_another_succeeds(monkeypatch) -> None:
    """A later success must not overwrite an earlier tool failure."""

    async def mixed_execute(self, db, lesson_id, name, arguments, *, source=None):
        args = json.loads(arguments)
        if args["ok"]:
            return {"ok": True, "sequence": 1}
        await asyncio.sleep(0.02)
        return {"ok": False, "error": "persist_failed"}

    monkeypatch.setattr(ToolRegistry, "execute_structured", mixed_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def mixed_call(call_id: str, ok: bool) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"ok": ok}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Everything is saved.", 0))
    qwen.push(mixed_call("ok", True))
    qwen.push(mixed_call("bad", False))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.create_response_count == 0
    assert socket.response_lifecycles["r1"].function_call_executed_ok is False


@pytest.mark.asyncio
async def test_cancelling_pending_tool_settles_its_response_gate(monkeypatch) -> None:
    """Cancelled tool work cannot leave a response gate latched forever."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def slow_execute(self, db, lesson_id, name, arguments, *, source=None):
        tool_started.set()
        await release_tool.wait()
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", slow_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_call())
    qwen.push(_delta("Must not play", 0))
    await asyncio.wait_for(tool_started.wait(), timeout=1.5)

    await hub._cancel_pending_work(socket, reason="superseded")
    release_tool.set()
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    gate = socket.response_gates["r1"]
    assert gate.calls["call_1"].settled is True
    assert gate.done_event is None
    assert socket.active_response_id is None
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
