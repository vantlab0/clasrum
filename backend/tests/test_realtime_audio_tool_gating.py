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

import app.realtime.hub as hub_module
from app.board.registry import ToolRegistry
from app.realtime.hub import (
    MSG_MIC_LOCK,
    MSG_MIC_UNLOCK,
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
        self.cancel_response_count = 0
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
        self.cancel_response_count += 1

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


def _call_settled(socket: LessonSocket, call_id: str, response_id: str = "r1") -> bool:
    """Safe predicate for managed tool tasks that may not be registered yet."""
    gate = socket.response_gates.get(response_id)
    return gate is not None and gate.calls.get(call_id) is not None and bool(
        gate.calls[call_id].settled
    )


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
async def test_audio_deltas_are_deduped_sorted_without_gap_recovery_on_reorder() -> None:
    """Out-of-order deltas that all arrive are sorted, not treated as a gap."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    reasons: list[str] = []
    original_request = hub._request_response

    async def record_reason(*args, **kwargs):
        reasons.append(str(kwargs.get("reason") or ""))
        return await original_request(*args, **kwargs)

    hub._request_response = record_reason  # type: ignore[method-assign]

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("D", 3))  # out of order, but 1 and 2 arrive before done
    qwen.push(_delta("B", 1))
    qwen.push(_delta("duplicate", 1))
    qwen.push(_delta("C", 2))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    deltas = socket.websocket.of_type(MSG_AUDIO_DELTA)
    assert [delta["data"] for delta in deltas] == ["A", "B", "C", "D"]
    assert [delta["sequence"] for delta in deltas] == [0, 1, 2, 3]
    assert socket.audio_gap_count == 0
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED) == []
    assert "audio_gap_repeat" not in reasons


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


@pytest.mark.asyncio
async def test_audio_arriving_well_after_old_grace_before_call_is_still_held(monkeypatch) -> None:
    """Audio before a late call must never be released by a timing heuristic."""

    async def slow_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": True, "sequence": 1}

    monkeypatch.setattr(ToolRegistry, "execute_structured", slow_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Question before the tool lookup", 0))
    # Longer than AUDIO_TOOL_GRACE_SECONDS (0.15s): the old gate would already
    # have played this chunk before the function call below was observed.
    await asyncio.sleep(0.25)
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    qwen.push(_call(name="ask_student"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == [
        "Question before the tool lookup"
    ]
    assert qwen.function_outputs


@pytest.mark.asyncio
async def test_function_call_found_only_in_done_manifest_runs_before_audio(monkeypatch) -> None:
    """Qwen's response.done.output is authoritative when argument events are absent."""
    executed: list[str] = []

    async def ok_execute(self, db, lesson_id, name, arguments, *, source=None):
        executed.append(name)
        return {"ok": True, "sequence": len(executed)}

    monkeypatch.setattr(ToolRegistry, "execute_structured", ok_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    done_with_manifest = {
        "type": "response.done",
        "response": {
            "id": "r1",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "manifest_call",
                    "name": "board_write_on_board",
                    "arguments": json.dumps({"text": "saved fact"}),
                }
            ],
        },
    }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Saved.", 0))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(done_with_manifest)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert executed == ["board_write_on_board"]
    assert qwen.function_outputs and json.loads(qwen.function_outputs[0][1])["ok"] is True
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["Saved."]
    assert socket.websocket.of_type(MSG_AUDIO_DONE)


@pytest.mark.asyncio
async def test_late_tool_call_after_finalize_is_rejected_without_side_effects(monkeypatch) -> None:
    """No tool may execute after the response boundary has been closed."""
    executed: list[str] = []

    async def spy_execute(self, db, lesson_id, name, arguments, *, source=None):
        executed.append(name)
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", spy_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("First answer", 0))
    qwen.push(_done())
    await _wait_for(lambda: qwen.create_response_count == 1)
    assert socket.response_gates["r1"].finalized is True

    qwen.push(_call(call_id="late_call"))
    await _wait_for(lambda: len(qwen.function_outputs) == 1)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert executed == []
    assert json.loads(qwen.function_outputs[0][1])["error"] == "late_tool_call"
    assert "late_call" in socket.response_gates["r1"].late_calls


@pytest.mark.asyncio
async def test_every_tool_in_multi_call_response_settles_before_audio(monkeypatch) -> None:
    """Audio is released once, after the slowest of several tool calls."""
    finished: set[str] = set()

    async def staggered_execute(self, db, lesson_id, name, arguments, *, source=None):
        args = json.loads(arguments)
        call_id = args["call_id"]
        await asyncio.sleep(args["delay"])
        finished.add(call_id)
        # A premature release would be observed here.
        assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
        return {"ok": True, "call_id": call_id}

    monkeypatch.setattr(ToolRegistry, "execute_structured", staggered_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call(call_id: str, delay: float) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"call_id": call_id, "delay": delay}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("All saved.", 0))
    qwen.push(call("one", 0.01))
    qwen.push(call("two", 0.04))
    qwen.push(call("three", 0.02))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert finished == {"one", "two", "three"}
    assert len(qwen.function_outputs) == 3
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["All saved."]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1


@pytest.mark.asyncio
async def test_wait_for_student_unlock_follows_buffered_question_audio() -> None:
    """The microphone opens only after the gated question is actually sent."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("What do you think?", 0))
    qwen.push(_call(name="wait_for_student"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    deltas = socket.websocket.of_type(MSG_AUDIO_DELTA)
    unlocks = socket.websocket.of_type(MSG_MIC_UNLOCK)
    assert len(deltas) == 1 and len(unlocks) == 1
    assert socket.websocket.sent.index(deltas[0]) < socket.websocket.sent.index(unlocks[0])
    assert socket.waiting_for_student is True
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_missing_response_done_is_recovered_without_releasing_audio(monkeypatch) -> None:
    """A stalled response cannot hold audio or the turn guard forever."""
    monkeypatch.setattr(hub_module, "RESPONSE_ACTIVITY_WATCHDOG_SECONDS", 0.05)
    monkeypatch.setattr(hub_module, "WATCHDOG_POLL_SECONDS", 0.05)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Untrusted partial speech", 0))
    await _wait_for(lambda: qwen.create_response_count == 1, timeout=2)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert qwen.cancel_response_count >= 1
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert socket.active_response_id is None
    assert socket.response_gates["r1"].finalized is True


@pytest.mark.asyncio
async def test_cancel_without_audio_does_not_claim_truncated_audio() -> None:
    """Cancellation must not fabricate a false audio.truncated event."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push({"type": "response.cancel", "response_id": "r1"})
    qwen.push(_done(status="cancelled"))
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED) == []


def test_late_done_cannot_overwrite_the_first_response_status() -> None:
    """A duplicate done event must not replace a completed response with cancel."""
    hub = LessonRealtimeHub()
    socket = _socket(FakeQwen())
    gate = hub._response_gate(socket, "r1")
    assert gate is not None

    hub._record_gate_done(gate, _done("completed"), "completed")
    hub._record_gate_done(gate, _done("cancelled"), "cancelled")

    assert gate.done_status == "completed"


@pytest.mark.asyncio
async def test_cancelled_response_without_done_settles_turn_guard(monkeypatch) -> None:
    """A cancelled gate must not hold active_response_id without a done event."""
    monkeypatch.setattr(hub_module, "DISCARDED_GATE_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr(hub_module, "WATCHDOG_POLL_SECONDS", 0.01)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Never spoken", 0))
    qwen.push({"type": "response.cancel", "response_id": "r1"})
    await _wait_for(
        lambda: socket.response_gates.get("r1") is not None
        and socket.response_gates["r1"].discarded,
        timeout=2,
    )
    await _wait_for(lambda: socket.active_response_id is None, timeout=2)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    gate = socket.response_gates["r1"]
    assert gate.finalized is True
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_failed_tool_without_done_settles_turn_guard(monkeypatch) -> None:
    """A failed tool with no later response.done must not latch the lesson."""

    async def failing_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": False, "error": "board_unavailable"}

    monkeypatch.setattr(ToolRegistry, "execute_structured", failing_execute)
    monkeypatch.setattr(hub_module, "DISCARDED_GATE_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr(hub_module, "WATCHDOG_POLL_SECONDS", 0.01)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("False confirmation", 0))
    qwen.push(_call())
    await _wait_for(
        lambda: socket.response_gates.get("r1") is not None
        and socket.response_gates["r1"].discarded,
        timeout=2,
    )
    await _wait_for(lambda: socket.active_response_id is None, timeout=2)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.response_gates["r1"].finalized is True
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_wait_for_student_rolls_back_when_another_tool_fails(monkeypatch) -> None:
    """An unspoken question must not leave the lesson waiting for an answer."""

    async def mixed_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": False, "error": "board_unavailable"}

    monkeypatch.setattr(ToolRegistry, "execute_structured", mixed_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("What do you think?", 0))
    qwen.push(_call(name="wait_for_student"))
    qwen.push(_call(call_id="call_2"))
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.waiting_for_student is False
    assert socket.mic_unlocked is False
    assert socket.websocket.of_type(MSG_MIC_UNLOCK) == []
    assert socket.websocket.of_type(MSG_MIC_LOCK)
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_lesson_finish_still_speaks_its_summary(monkeypatch) -> None:
    """finishing the lesson must not discard the tutor's final speech."""

    async def ok_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", ok_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("That completes the lesson.", 0))
    qwen.push(_call(name="lesson_finish"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.lesson_finished is True
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == [
        "That completes the lesson."
    ]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED) == []
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_create_guard_outlives_a_slow_in_flight_create(monkeypatch) -> None:
    """No second response.create while the first transport call is still running."""
    monkeypatch.setattr(hub_module, "RESPONSE_CREATED_GUARD_SECONDS", 0.01)
    monkeypatch.setattr(hub_module, "WATCHDOG_POLL_SECONDS", 0.01)
    monkeypatch.setattr(hub_module, "RESPONSE_REQUEST_COOLDOWN_SECONDS", 0.0)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()

    async def slow_create() -> None:
        await asyncio.sleep(0.15)
        qwen.create_response_count += 1

    qwen.create_response = slow_create  # type: ignore[method-assign]
    socket = _socket(qwen)

    first = asyncio.create_task(hub._request_response(socket, reason="first", force=True))
    await asyncio.sleep(0.05)
    second = await hub._request_response(socket, reason="second", force=True)
    assert second is False
    assert await first is True
    assert qwen.create_response_count == 1
    await _wait_for(lambda: not socket.response_request_pending, timeout=2)


@pytest.mark.asyncio
async def test_late_tool_output_failure_does_not_kill_qwen_loop() -> None:
    """A transport error while acknowledging a late call must not stop the loop."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()

    async def broken_output(call_id: str, output: str, *, continue_response: bool = False) -> None:
        raise RuntimeError("transport closed")

    qwen.function_call_output = broken_output  # type: ignore[method-assign]
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Hello", 0))
    qwen.push(_done())
    qwen.push(_call(call_id="late_call", name="board_write_on_board"))
    qwen.push(
        {
            "type": "response.audio_transcript.done",
            "response_id": "r1",
            "speaker": "tutor",
            "transcript": "Still teaching.",
        }
    )
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_TRANSCRIPT_ENTRY)
    assert socket.response_gates["r1"].late_calls == {"late_call"}


@pytest.mark.asyncio
async def test_confirmed_missing_audio_truncates_and_repeats_once() -> None:
    """A missing chunk at audio.done discards partial speech and asks for a repeat."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    reasons: list[str] = []
    original_request = hub._request_response

    async def record_reason(*args, **kwargs):
        reasons.append(str(kwargs.get("reason") or ""))
        return await original_request(*args, **kwargs)

    hub._request_response = record_reason  # type: ignore[method-assign]

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("C", 2))  # sequence 1 never arrives
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    truncated = socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert [m["reason"] for m in truncated] == ["audio_sequence_missing"]
    assert socket.audio_gap_count == 1
    assert len(qwen.prompt_texts) == 1
    repeat = qwen.prompt_texts[0].lower()
    assert "repeat" in repeat
    assert "do not call any board tool" in repeat
    assert reasons.count("audio_gap_repeat") == 1
    assert qwen.create_response_count == 1
    assert socket.response_gates["r1"].finalized is True
    assert socket.active_response_id is None
    assert socket.audio_gap_repeats == 1


@pytest.mark.asyncio
async def test_audio_only_repeat_rejects_board_tools(monkeypatch) -> None:
    """A gap repeat cannot re-run board writes through a best-effort prompt."""
    async def forbidden_execute(self, db, lesson_id, name, arguments, *, source=None):
        raise AssertionError("audio-only recovery must not execute tools")

    monkeypatch.setattr(ToolRegistry, "execute_structured", forbidden_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("C", 2))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    qwen.push(_created("r2"))
    qwen.push(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "r2",
            "call_id": "repeat_call",
            "name": "board_write_on_board",
            "arguments": "{}",
        }
    )
    qwen.push({"type": "response.done", "response": {"id": "r2", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.response_gates["r2"].audio_only_recovery is True
    assert any("gap_recovery_audio_only" in output for _, output, _ in qwen.function_outputs)
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []


@pytest.mark.asyncio
async def test_missing_sequence_field_never_triggers_gap_recovery() -> None:
    """Without source sequences there is nothing to confirm at audio.done."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)
    reasons: list[str] = []
    original_request = hub._request_response

    async def record_reason(*args, **kwargs):
        reasons.append(str(kwargs.get("reason") or ""))
        return await original_request(*args, **kwargs)

    hub._request_response = record_reason  # type: ignore[method-assign]

    def delta_without_sequence(data: str) -> dict[str, Any]:
        return {
            "type": "response.audio.delta",
            "response_id": "r1",
            "delta": data,
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(delta_without_sequence("A"))
    qwen.push(delta_without_sequence("B"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["A", "B"]
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED) == []
    assert socket.audio_gap_count == 0
    assert "audio_gap_repeat" not in reasons


@pytest.mark.asyncio
async def test_failed_response_with_gap_does_not_repeat() -> None:
    """A failed/cancelled turn must not generate a fresh teaching turn."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("C", 2))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done(status="failed"))
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.prompt_texts == []
    assert qwen.create_response_count == 0
    assert socket.audio_gap_repeats == 0


@pytest.mark.asyncio
async def test_late_delta_after_confirmed_gap_is_not_played() -> None:
    """A chunk arriving after the boundary cannot resurrect discarded speech."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("C", 2))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    qwen.push(_delta("B", 1))
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)


@pytest.mark.asyncio
async def test_gap_repeat_is_bounded_to_one_attempt() -> None:
    """A second consecutive gap truncates but does not spawn another repeat."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("A", 0))
    qwen.push(_delta("C", 2))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    qwen.push(_created("r2"))
    qwen.push({"type": "response.audio.delta", "response_id": "r2", "delta": "A", "sequence": 0})
    qwen.push({"type": "response.audio.delta", "response_id": "r2", "delta": "C", "sequence": 2})
    qwen.push({"type": "response.audio.done", "response_id": "r2"})
    qwen.push({"type": "response.done", "response": {"id": "r2", "status": "completed"}})
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert len(qwen.prompt_texts) == 1
    assert socket.audio_gap_repeats == 1
    assert len(socket.websocket.of_type(MSG_AUDIO_TRUNCATED)) == 2


@pytest.mark.asyncio
async def test_first_tool_settles_then_second_tool_arrives_500ms_later(monkeypatch) -> None:
    """A settled tool must not release speech before response.done closes the manifest."""
    executed: list[str] = []

    async def fast_execute(self, db, lesson_id, name, arguments, *, source=None):
        call_id = json.loads(arguments)["call_id"]
        executed.append(call_id)
        # The regressed implementation had already flushed audio at this point.
        assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
        return {"ok": True, "call_id": call_id}

    monkeypatch.setattr(ToolRegistry, "execute_structured", fast_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call(call_id: str) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"call_id": call_id}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Complete answer", 0))
    qwen.push(call("A"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await _wait_for(lambda: _call_settled(socket, "A"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []

    # Tool B really appears half a second after A already settled.
    await asyncio.sleep(0.5)
    qwen.push(call("B"))
    await _wait_for(lambda: _call_settled(socket, "B"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []

    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert executed == ["A", "B"]
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["Complete answer"]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1


@pytest.mark.asyncio
async def test_manifest_only_tool_still_gates_audio_after_earlier_tool_settled(monkeypatch) -> None:
    """response.done.output may reveal the final tool long after an earlier one settled."""
    executed: list[str] = []
    manifest_tool_started = asyncio.Event()
    release_manifest_tool = asyncio.Event()

    async def execute(self, db, lesson_id, name, arguments, *, source=None):
        call_id = json.loads(arguments)["call_id"]
        if call_id == "B":
            manifest_tool_started.set()
            await release_manifest_tool.wait()
        executed.append(call_id)
        assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
        return {"ok": True, "call_id": call_id}

    monkeypatch.setattr(ToolRegistry, "execute_structured", execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call_a() -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": "A",
            "name": "board_write_on_board",
            "arguments": json.dumps({"call_id": "A"}),
        }

    done_with_manifest = {
        "type": "response.done",
        "response": {
            "id": "r1",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "B",
                    "name": "board_write_on_board",
                    "arguments": json.dumps({"call_id": "B"}),
                }
            ],
        },
    }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Saved before the manifest", 0))
    qwen.push(call_a())
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await _wait_for(lambda: _call_settled(socket, "A"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    qwen.push(done_with_manifest)
    await asyncio.wait_for(manifest_tool_started.wait(), timeout=1.5)
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    release_manifest_tool.set()
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert executed == ["A", "B"]
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == [
        "Saved before the manifest"
    ]


@pytest.mark.asyncio
async def test_multiple_tools_settle_in_different_orders_before_audio(monkeypatch) -> None:
    """The slowest tool, not the last observed one, decides when speech is safe."""
    finished: list[str] = []

    async def staggered_execute(self, db, lesson_id, name, arguments, *, source=None):
        args = json.loads(arguments)
        await asyncio.sleep(args["delay"])
        finished.append(args["call_id"])
        assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", staggered_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call(call_id: str, delay: float) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"call_id": call_id, "delay": delay}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("All saved", 0))
    qwen.push(call("slow", 0.08))
    qwen.push(call("fast", 0.01))
    qwen.push(call("middle", 0.04))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert finished == ["fast", "middle", "slow"]
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["All saved"]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1


@pytest.mark.asyncio
async def test_success_then_failure_after_done_never_releases_speech(monkeypatch) -> None:
    """One early success cannot speak over a sibling tool that fails later."""

    async def mixed_execute(self, db, lesson_id, name, arguments, *, source=None):
        if json.loads(arguments)["ok"]:
            return {"ok": True}
        await asyncio.sleep(0.03)
        return {"ok": False, "error": "persist_failed"}

    monkeypatch.setattr(ToolRegistry, "execute_structured", mixed_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call(call_id: str, ok: bool) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"ok": ok}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Do not trust this confirmation", 0))
    qwen.push(call("good", True))
    qwen.push(call("bad", False))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())

    await _wait_for(lambda: _call_settled(socket, "good"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert socket.response_lifecycles["r1"].function_call_executed_ok is False
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_wait_for_student_delayed_done_unlocks_only_after_question_audio() -> None:
    """The microphone stays locked until the gated question is actually spoken."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("What do you think?", 0))
    qwen.push(_call(name="wait_for_student"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await _wait_for(lambda: _call_settled(socket, "call_1"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_MIC_UNLOCK) == []
    assert socket.waiting_for_student is True

    await asyncio.sleep(0.5)
    qwen.push(_done())
    await _wait_for(lambda: bool(socket.websocket.of_type(MSG_MIC_UNLOCK)))
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    deltas = socket.websocket.of_type(MSG_AUDIO_DELTA)
    unlocks = socket.websocket.of_type(MSG_MIC_UNLOCK)
    assert [d["data"] for d in deltas] == ["What do you think?"]
    assert len(unlocks) == 1
    assert socket.websocket.sent.index(deltas[0]) < socket.websocket.sent.index(unlocks[0])
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_lesson_finish_delayed_done_still_speaks_final_summary(monkeypatch) -> None:
    """lesson_finish settles early but its closing speech waits for response.done."""

    async def ok_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", ok_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("That completes the lesson.", 0))
    qwen.push(_call(name="lesson_finish"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await _wait_for(lambda: _call_settled(socket, "call_1"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    await asyncio.sleep(0.5)
    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.lesson_finished is True
    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == [
        "That completes the lesson."
    ]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED) == []
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_cancellation_while_two_tools_execute_discards_all_audio(monkeypatch) -> None:
    """A fast tool settling before cancellation may not release the gated answer."""
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()

    async def execute(self, db, lesson_id, name, arguments, *, source=None):
        call_id = json.loads(arguments)["call_id"]
        if call_id == "slow":
            slow_started.set()
            await release_slow.wait()
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    def call(call_id: str) -> dict[str, Any]:
        return {
            "type": "response.function_call_arguments.done",
            "response_id": "r1",
            "call_id": call_id,
            "name": "board_write_on_board",
            "arguments": json.dumps({"call_id": call_id}),
        }

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Never spoken", 0))
    qwen.push(call("fast"))
    qwen.push(call("slow"))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await asyncio.wait_for(slow_started.wait(), timeout=1.5)
    await _wait_for(lambda: _call_settled(socket, "fast"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    qwen.push({"type": "response.cancel", "response_id": "r1"})
    await _wait_for(lambda: socket.response_gates["r1"].discarded is True)
    release_slow.set()
    qwen.push(_done(status="cancelled"))
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_no_function_call_response_releases_audio_only_at_done() -> None:
    """A pure-speech response is still gated until response.done arrives."""
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Pure speech", 0))
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await asyncio.sleep(0.2)
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []

    qwen.push(_done())
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert [d["data"] for d in socket.websocket.of_type(MSG_AUDIO_DELTA)] == ["Pure speech"]
    assert len(socket.websocket.of_type(MSG_AUDIO_DONE)) == 1


@pytest.mark.asyncio
async def test_timed_out_tool_discards_buffered_audio(monkeypatch) -> None:
    """A tool that never finishes cannot hold speech or leave audio buffered forever."""
    monkeypatch.setattr(hub_module, "TOOL_EXECUTION_TIMEOUT_SECONDS", 0.05)
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
    qwen.push(_delta("Untrusted speech", 0))
    qwen.push(_call())
    qwen.push({"type": "response.audio.done", "response_id": "r1"})
    qwen.push(_done())

    await asyncio.wait_for(tool_started.wait(), timeout=1.5)
    await _wait_for(
        lambda: bool(socket.websocket.of_type(MSG_AUDIO_TRUNCATED)), timeout=1.5
    )
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.response_gates["r1"].finalized is True

    release_tool.set()
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_DONE) == []
    assert qwen.create_response_count == 0


@pytest.mark.asyncio
async def test_done_missing_after_tool_settles_discards_instead_of_buffering_forever(monkeypatch) -> None:
    """A settled tool with no done still resolves via the watchdog, without speech."""
    monkeypatch.setattr(hub_module, "RESPONSE_ACTIVITY_WATCHDOG_SECONDS", 0.05)
    monkeypatch.setattr(hub_module, "WATCHDOG_POLL_SECONDS", 0.02)

    async def ok_execute(self, db, lesson_id, name, arguments, *, source=None):
        return {"ok": True}

    monkeypatch.setattr(ToolRegistry, "execute_structured", ok_execute)
    hub = LessonRealtimeHub()
    qwen = FakeQwen()
    socket = _socket(qwen)

    task = asyncio.create_task(hub._qwen_loop(socket, "lesson-1"))
    qwen.push(_created())
    qwen.push(_delta("Unanswered question", 0))
    qwen.push(_call())
    qwen.push({"type": "response.audio.done", "response_id": "r1"})

    await _wait_for(lambda: _call_settled(socket, "call_1"))
    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []

    await _wait_for(lambda: qwen.create_response_count == 1, timeout=2)
    await qwen.queue.put(None)
    await asyncio.wait_for(task, timeout=2)

    assert socket.websocket.of_type(MSG_AUDIO_DELTA) == []
    assert socket.websocket.of_type(MSG_AUDIO_TRUNCATED)
    assert socket.response_gates["r1"].finalized is True
    assert socket.active_response_id is None
