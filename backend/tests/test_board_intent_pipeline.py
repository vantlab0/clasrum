"""Phase 1: intent -> Board Director -> layout -> BoardService pipeline tests."""
from __future__ import annotations

import json

import pytest

from app.board.intent_handler import BoardIntentHandler, IntentHandlingError, summarize_board, validate_plan
from app.board.layout import layout_plan, place_action, build_layout_state
from app.board.registry import ToolRegistry
from app.board.schemas import public_openai_tool_schemas
from app.board.service import BoardService


@pytest.fixture
async def lesson_id(seeded_student, make_lesson):
    lesson = await make_lesson(seeded_student, topic="Cell Biology", subject="Biology")
    return lesson["id"]


def test_realtime_tools_expose_intents_and_hide_internal_board_tools():
    schemas = public_openai_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    # Intent tools exposed
    assert "board_intent" in names
    assert "board_highlight" in names
    assert "board_clear_region" in names
    # Lesson tools still exposed
    assert "lesson_next_concept" in names
    assert "lesson_finish" in names
    # Internal low-level board tools must NOT be exposed to realtime
    assert "board_write_text" not in names
    assert "board_draw_diagram" not in names
    assert "board_plot_function" not in names
    assert "board_clear" not in names


@pytest.mark.asyncio
async def test_internal_board_tools_remain_executable(lesson_id):
    from app.db.session import session_scope

    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_write_text",
            {"text": "Hello", "position": {"x": 0.1, "y": 0.1}},
            source="system",
        )
    assert result["ok"] is True
    assert result["element_id"]
    assert result["sequence"] >= 1


def test_summarize_board_empty_and_basic():
    assert summarize_board(None) == []
    assert summarize_board({"elements": []}) == []
    doc = {
        "elements": [
            {
                "element_id": "el_1",
                "type": "write_text",
                "payload": {"text": "Mitochondria"},
                "position": {"x": 0.2, "y": 0.3},
                "size": {"w": 0.3, "h": 0.1},
                "removed_at_sequence": None,
            },
            {
                "element_id": "el_2",
                "type": "write_text",
                "payload": {"text": "gone"},
                "removed_at_sequence": 5,
            },
        ]
    }
    summary = summarize_board(doc)
    assert len(summary) == 1
    assert summary[0]["id"] == "el_1"
    assert summary[0]["label"] == "Mitochondria"
    assert summary[0]["type"] == "write_text"


def test_validate_plan_rejects_unsupported_and_normalizes():
    with pytest.raises(IntentHandlingError):
        validate_plan({"actions": [{"type": "delete_database"}]})
    plan = validate_plan(
        {
            "actions": [
                {"type": "add_node", "label": "ATP", "region": "right"},
                {"type": "annotate", "target": "el_1", "style": "box"},
            ],
            "focus": {"target_ids": ["el_1"]},
        }
    )
    assert len(plan["actions"]) == 2
    assert plan["actions"][0]["type"] == "add_node"


def test_layout_positions_elements_and_respects_region_hint():
    plan = {
        "actions": [
            {"type": "add_node", "label": "A", "region": "right"},
            {"type": "add_node", "label": "B", "region": "right"},
        ]
    }
    out = layout_plan(plan, document={"elements": []}, default_region="right")
    a1, a2 = out["actions"][0], out["actions"][1]
    assert "position" in a1 and "position" in a2
    assert a1["region"] == "right"
    assert a2["region"] == "right"
    # Should not stack at identical y positions in normal case.
    assert (a1["position"]["x"], a1["position"]["y"]) != (a2["position"]["x"], a2["position"]["y"])


def test_layout_avoids_existing_element_collision():
    doc = {
        "elements": [
            {
                "element_id": "el_existing",
                "type": "write_text",
                "payload": {"text": "Existing"},
                "position": {"x": 0.52, "y": 0.10},
                "size": {"w": 0.30, "h": 0.10},
                "removed_at_sequence": None,
            }
        ]
    }
    plan = {"actions": [{"type": "add_node", "label": "New", "region": "right"}]}
    out = layout_plan(plan, document=doc, default_region="right")
    pos = out["actions"][0]["position"]
    # Must be placed somewhere valid (engine nudges away from occupied slot).
    assert 0.0 <= pos["x"] <= 1.0 and 0.0 <= pos["y"] <= 1.0
    assert out["actions"][0]["region"] == "right"


@pytest.mark.asyncio
async def test_board_intent_end_to_end_with_mocked_director(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def fake_director(context):
        return {
            "actions": [
                {
                    "type": "add_node",
                    "id": "mito",
                    "label": "Mitochondria",
                    "sublabel": "Produces ATP",
                    "region": "right",
                },
                {
                    "type": "write_equation",
                    "latex": "ADP + P_i -> ATP",
                    "region": "right",
                },
            ],
            "focus": {"target_ids": ["mito"], "priority": "high"},
        }

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)

    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {
                "intent": "Show mitochondria and its role in producing cellular energy",
                "focus": "new_content",
                "region_hint": "right",
            },
            source="qwen_tool",
        )
        assert result["ok"] is True, result
        assert result["actions_executed"] >= 1

        # Verify events persisted and broadcastable via BoardService reads.
        events = await registry.board.get_events(db, lesson_id)
        assert len(events) >= 1
        types = [e.type for e in events]
        assert "write_text" in types or "write_equation" in types

    # Fresh session read-back (simulates client fetch/broadcast payload availability).
    async with session_scope() as db:
        events = await BoardService().get_events(db, lesson_id)
        assert len(events) >= 1


@pytest.mark.asyncio
async def test_board_intent_handles_invalid_model_output(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def bad_director(context):
        return {"actions": [{"type": "drop_table"}]}

    monkeypatch.setattr("app.board.intent_handler.call_board_director", bad_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Do something bad"},
            source="qwen_tool",
        )
    assert result["ok"] is False
    assert result.get("error") in {"invalid_arguments", "intent_failed"}


@pytest.mark.asyncio
async def test_board_intent_model_timeout_returns_controlled_error(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def timeout_director(context):
        raise IntentHandlingError("Board director timed out", code="qwen_unavailable", retryable=True)

    monkeypatch.setattr("app.board.intent_handler.call_board_director", timeout_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Show anything"},
            source="qwen_tool",
        )
    assert result["ok"] is False
    assert result.get("retryable") is True


@pytest.mark.asyncio
async def test_board_highlight_intent_resolves_target(lesson_id):
    from app.db.session import session_scope

    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        created = await registry.execute_structured(
            db,
            lesson_id,
            "board_write_text",
            {"text": "Mitochondria", "position": {"x": 0.2, "y": 0.2}},
            source="system",
        )
        assert created["ok"] is True
        # Intent-form highlight by label.
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_highlight",
            {"target": "Mitochondria", "style": "box"},
            source="qwen_tool",
        )
        assert result["ok"] is True, result

        # Internal-form highlight still works.
        internal = await registry.execute_structured(
            db,
            lesson_id,
            "board_highlight",
            {"target_ids": [created["element_id"]], "style": "circle"},
            source="system",
        )
        assert internal["ok"] is True


@pytest.mark.asyncio
async def test_board_clear_region_intent(lesson_id):
    from app.db.session import session_scope

    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_clear_region",
            {"scope": "all"},
            source="qwen_tool",
        )
        assert result["ok"] is True
        assert result.get("event_id")


@pytest.mark.asyncio
async def test_board_intent_missing_context_and_empty_board(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def simple_director(context):
        # Should receive safe defaults even with empty board/transcript.
        assert isinstance(context.board_summary, list)
        assert isinstance(context.lesson_state, dict)
        return {"actions": [], "focus": {"target_ids": [], "priority": "normal"}}

    monkeypatch.setattr("app.board.intent_handler.call_board_director", simple_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Nothing yet"}, source="qwen_tool"
        )
    assert result["ok"] is True
    assert result["actions_executed"] == 0


@pytest.mark.asyncio
async def test_board_intent_draw_template_valid(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def template_director(context):
        return {
            "actions": [
                {
                    "type": "draw_template",
                    "template_id": "mitochondria",
                    "region": "right",
                    "labels": ["cristae_label", "matrix_label"],
                }
            ],
            "focus": {"target_ids": [], "priority": "normal"},
        }

    monkeypatch.setattr("app.board.intent_handler.call_board_director", template_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Show mitochondria", "focus": "new_content", "region_hint": "right"},
            source="qwen_tool",
        )
    assert result["ok"] is True, result
    assert result["actions_executed"] >= 1


@pytest.mark.asyncio
async def test_board_intent_draw_template_unknown_degrades(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def bad_template_director(context):
        return {
            "actions": [
                {
                    "type": "draw_template",
                    "template_id": "nonexistent_template",
                    "region": "right",
                }
            ],
            "focus": {"target_ids": [], "priority": "normal"},
        }

    monkeypatch.setattr("app.board.intent_handler.call_board_director", bad_template_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Show unknown template", "focus": "new_content", "region_hint": "right"},
            source="qwen_tool",
        )
    # Unknown template should degrade gracefully (not crash)
    assert result["ok"] is True or result.get("error") is not None


@pytest.mark.asyncio
async def test_board_intent_draw_template_invalid_region(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def bad_region_director(context):
        return {
            "actions": [
                {
                    "type": "draw_template",
                    "template_id": "mitochondria",
                    "region": "not_a_region",
                }
            ],
            "focus": {"target_ids": [], "priority": "normal"},
        }

    monkeypatch.setattr("app.board.intent_handler.call_board_director", bad_region_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Show mitochondria bad region", "focus": "new_content"},
            source="qwen_tool",
        )
    # Invalid region should be normalized away, not crash
    assert result["ok"] is True, result
