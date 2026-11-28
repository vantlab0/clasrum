"""Regression tests for duplicate tool calls and repeated logical board events."""
from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import WebSocket

from app.board.registry import _duplicate_plan_result, _duplicate_text_result
from app.realtime.hub import LessonRealtimeHub, LessonSocket

_TEXT_DOCUMENT: dict[str, Any] = {
    "elements": [{
        "element_id": "el_1",
        "event_id": "evt_1",
        "type": "write_text",
        "created_sequence": 1,
        "payload": {
            "text": "Photosynthesis converts light into sugar",
            "style": "body",
            "heading_level": 0,
            "color": "primary",
        },
    }]
}

_PLAN_DOCUMENT: dict[str, Any] = {
    "elements": [{
        "element_id": "visual_1",
        "type": "draw_plan",
        "created_sequence": 2,
        "payload": {"context": {"intent": "draw the photosynthesis cycle"}},
    }]
}


def test_repeated_text_write_is_acknowledged_without_a_new_element():
    duplicate = _duplicate_text_result(
        _TEXT_DOCUMENT,
        {
            "text": "  photosynthesis  converts light into sugar ",
            "style": "BODY",
            "heading_level": 0,
            "color": "primary",
        },
    )
    assert duplicate is not None
    assert duplicate["duplicate"] is True
    assert duplicate["element_id"] == "el_1"

    assert _duplicate_text_result(
        _TEXT_DOCUMENT,
        {"text": "A different line", "style": "body"},
    ) is None


def test_repeated_visual_intent_is_acknowledged_without_a_new_plan():
    duplicate = _duplicate_plan_result(
        _PLAN_DOCUMENT, "Draw   the photosynthesis cycle."
    )
    assert duplicate is not None
    assert duplicate["duplicate"] is True
    assert duplicate["element_id"] == "visual_1"
    assert _duplicate_plan_result(_PLAN_DOCUMENT, "draw a different cycle") is None


class _FakeQwen:
    def __init__(self) -> None:
        self.outputs: list[dict[str, Any]] = []

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = False
    ) -> None:
        import json

        self.outputs.append(
            {
                "call_id": call_id,
                "output": json.loads(output),
                "continue_response": continue_response,
            }
        )


@pytest.mark.asyncio
async def test_replayed_function_call_executes_once_and_resends_cached_result(monkeypatch):
    hub = LessonRealtimeHub()
    qwen = _FakeQwen()
    socket = LessonSocket(
        websocket=cast(WebSocket, object()),
        lesson_id="lesson_1",
        student_id="student_1",
        lease_id="lease_1",
        qwen=cast(Any, qwen),
    )
    calls: list[str] = []

    async def fake_execute(
        self: LessonRealtimeHub,
        current: LessonSocket,
        lesson_id: str,
        event: dict[str, Any],
        response_id: str | None = None,
    ) -> dict[str, Any]:
        import json

        calls.append(str(event["call_id"]))
        result = {"ok": True, "sequence": len(calls)}
        await current.qwen.function_call_output(
            str(event["call_id"]), json.dumps(result)
        )
        return result

    monkeypatch.setattr(LessonRealtimeHub, "_execute_qwen_tool_call", fake_execute)
    event = {"call_id": "call_7", "name": "board_write_on_board", "arguments": "{}"}
    await hub._handle_qwen_tool_call(socket, "lesson_1", event, response_id="resp_1")
    await asyncio.sleep(0)
    await hub._handle_qwen_tool_call(socket, "lesson_1", event, response_id="resp_1")

    assert calls == ["call_7"]
    assert len(qwen.outputs) == 2
    assert qwen.outputs[0]["output"] == qwen.outputs[1]["output"]
    # Replays resend the cached result but never request another response.
    assert qwen.outputs[1]["continue_response"] is False
