"""Automatic server-owned board cleanup when the text column fills."""
from __future__ import annotations

import pytest

from app.board.registry import ToolRegistry
from app.board.service import EVENT_CLEAR, BoardService
from app.db.constants import BoardEventSource
from app.db.session import session_scope


@pytest.mark.asyncio
async def test_board_write_auto_clears_and_continues_below(seeded_student, make_lesson):
    lesson = await make_lesson(seeded_student, topic="Cell Biology", subject="Biology")
    registry = ToolRegistry(board=BoardService())

    async with session_scope() as db:
        for index in range(40):
            result = await registry.execute_structured(
                db,
                lesson["id"],
                "board_write_on_board",
                {
                    "text": f"Teaching line {index}",
                    "style": "body",
                    "position": "next_line",
                    "writing_style": "instant",
                },
                source="qwen_tool",
            )
            assert result["ok"] is True, result
        events = await BoardService().get_events(db, lesson["id"])

    clears = [event for event in events if event.type == EVENT_CLEAR]
    assert clears, "a full text column must trigger a server-owned clear"
    assert clears[0].source == BoardEventSource.SYSTEM

    clear_sequence = clears[0].sequence
    before = [
        event for event in events
        if event.type == "write_text" and event.sequence < clear_sequence
    ]
    after = [
        event for event in events
        if event.type == "write_text" and event.sequence > clear_sequence
    ]
    assert before and after

    # The last pre-clear line is near the bottom; the first post-clear line
    # starts a fresh down-the-page column near the top.
    assert before[-1].payload["position"]["y"] > 0.5
    assert after[0].payload["position"]["y"] < before[-1].payload["position"]["y"]
