"""Phase 4: generative drawing pipeline (intent -> Qwen plan -> draw_plan event)."""
from __future__ import annotations

import json

import pytest

from app.board.intent_handler import (
    BoardIntentHandler,
    IntentHandlingError,
    call_board_director,
    fit_plan_to_region,
    summarize_board,
    validate_drawing_plan,
)
from app.board.registry import ToolRegistry
from app.board.schemas import public_openai_tool_schemas
from app.board.service import BoardService


# ---------------------------------------------------------------------------
# mocked Qwen drawing plans (deterministic; not templates of the product)
# ---------------------------------------------------------------------------


def mock_plan() -> dict:
    """A minimal but protocol-complete mocked Qwen response."""
    return {
        "protocol": "clasrum.drawing",
        "version": 1,
        "title": "Wave refraction",
        "elements": [
            {
                "id": "surface",
                "kind": "path",
                "geometry": {
                    "segments": [
                        {"op": "move", "point": {"x": 0.1, "y": 0.5}},
                        {"op": "cubic", "c1": {"x": 0.3, "y": 0.35}, "c2": {"x": 0.6, "y": 0.35}, "point": {"x": 0.9, "y": 0.45}},
                        {"op": "line", "point": {"x": 0.9, "y": 0.8}},
                        {"op": "line", "point": {"x": 0.1, "y": 0.8}},
                        {"op": "close"},
                    ]
                },
                "style": {"stroke_color": "blue", "stroke_width": 2.0, "fill_color": "blue", "fill_opacity": 0.15},
                "layer": 0,
                "stage": 1,
                "duration_ms": 900,
                "delay_ms": 0,
            },
            {
                "id": "ray1",
                "kind": "arrow",
                "geometry": {"start": {"x": 0.15, "y": 0.2}, "end": {"x": 0.35, "y": 0.45}, "curve": 0.1},
                "style": {"stroke_color": "yellow", "stroke_width": 1.5, "dash": [6, 4]},
                "layer": 1,
                "stage": 2,
                "duration_ms": 500,
                "delay_ms": 100,
            },
            {
                "id": "depth_label",
                "kind": "text",
                "geometry": {"text": "shallow water", "position": {"x": 0.4, "y": 0.75}, "align": "left"},
                "layer": 2,
                "stage": 3,
                "duration_ms": 300,
            },
        ],
        "stages": [
            {"stage": 1, "description": "water body outline", "element_ids": ["surface"], "pause_ms": 200},
            {"stage": 2, "description": "incident ray", "element_ids": ["ray1"]},
            {"stage": 3, "description": "labels", "element_ids": ["depth_label"]},
        ],
        "focus": {"target_ids": ["surface"], "priority": "high"},
        "triggers": [{"phrase": "refraction", "target": "surface", "action": "highlight"}],
    }


@pytest.fixture
async def lesson_id(seeded_student, make_lesson):
    lesson = await make_lesson(seeded_student, topic="Wave Optics", subject="Physics")
    return lesson["id"]


# ---------------------------------------------------------------------------
# realtime tool surface
# ---------------------------------------------------------------------------


def test_realtime_tools_expose_intents_and_hide_internal_board_tools():
    schemas = public_openai_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "board_intent" in names
    assert "board_highlight" in names
    assert "board_clear_region" in names
    assert "lesson_next_concept" in names
    assert "board_write_text" not in names
    assert "board_draw_diagram" not in names
    # The predefined-template tool is gone for good.
    assert "board_draw_template" not in names
    assert not any("template" in name for name in names)


# ---------------------------------------------------------------------------
# plan validation
# ---------------------------------------------------------------------------


def test_validate_drawing_plan_accepts_valid_plan():
    payload = validate_drawing_plan(mock_plan())
    assert payload["protocol"] == "clasrum.drawing"
    assert len(payload["elements"]) == 3
    assert payload["elements"][0]["kind"] == "path"
    assert payload["stages"][0]["element_ids"] == ["surface"]


def test_validate_drawing_plan_rejects_unsupported_kind():
    plan = mock_plan()
    plan["elements"][0]["kind"] = "hatch"
    with pytest.raises(IntentHandlingError) as exc:
        validate_drawing_plan(plan)
    assert exc.value.code == "invalid_arguments"


def test_validate_drawing_plan_rejects_out_of_bounds_coordinates():
    plan = mock_plan()
    plan["elements"][1]["geometry"]["start"] = {"x": 1.7, "y": 0.2}
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_nan_and_infinite():
    plan = mock_plan()
    plan["elements"][0]["geometry"]["segments"][1]["c1"] = {"x": float("inf"), "y": 0.1}
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_unknown_stage_reference():
    plan = mock_plan()
    plan["elements"][1]["stage"] = 9
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_undeclared_stage_element():
    plan = mock_plan()
    plan["stages"][0]["element_ids"] = ["ghost_element"]
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_oversized_path():
    plan = mock_plan()
    plan["elements"][0]["geometry"]["segments"] = [
        {"op": "line", "point": {"x": 0.1 + i * 0.001, "y": 0.2}} for i in range(200)
    ]
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_duplicate_element_ids():
    plan = mock_plan()
    plan["elements"][1]["id"] = "surface"
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_text_only_plan():
    plan = mock_plan()
    plan["elements"] = [e for e in plan["elements"] if e["kind"] == "text"]
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_unsafe_text():
    plan = mock_plan()
    plan["elements"][2]["geometry"]["text"] = "bad\x07bell"
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


def test_validate_drawing_plan_rejects_fill_on_open_element():
    plan = mock_plan()
    plan["elements"][1]["style"] = {"fill_color": "blue", "fill_opacity": 0.3}
    with pytest.raises(IntentHandlingError):
        validate_drawing_plan(plan)


# ---------------------------------------------------------------------------
# region fitting
# ---------------------------------------------------------------------------


def test_fit_plan_to_region_maps_out_of_region_plan():
    plan = validate_drawing_plan(mock_plan())
    # Requested region: right half. The mock plan spans the full width.
    fitted = fit_plan_to_region(plan, (0.55, 0.05, 0.4, 0.85))
    xs = [b[0] for b in _element_boxes(fitted)]
    rights = [b[0] + b[2] for b in _element_boxes(fitted)]
    assert min(xs) >= 0.55 - 1e-6
    assert max(rights) <= 0.95 + 1e-6


def test_fit_plan_to_region_leaves_fitting_plan_untouched():
    plan = validate_drawing_plan(mock_plan())
    fitted = fit_plan_to_region(plan, (0.0, 0.0, 1.0, 1.0))
    assert fitted == plan


def _element_boxes(payload: dict) -> list[tuple[float, float, float, float]]:
    from app.board.drawing_protocol import plan_element_bounds

    return plan_element_bounds(payload)


# ---------------------------------------------------------------------------
# end-to-end pipeline with a mocked director
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_intent_generates_and_persists_draw_plan(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def fake_director(context):
        assert context.lesson_state.get("topic") == "Wave Optics"
        assert isinstance(context.board_summary, list)
        return mock_plan()

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)

    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {
                "intent": "Show how light refracts when entering shallow water",
                "focus": "new_content",
                "region_hint": "right",
            },
            source="qwen_tool",
        )
        assert result["ok"] is True, result
        assert result["plan"]["element_count"] == 3
        assert result["plan"]["duration_ms"] > 0

        events = await registry.board.get_events(db, lesson_id)
        assert len(events) == 1
        assert events[0].type == "draw_plan"
        assert events[0].payload["protocol"] == "clasrum.drawing"
        assert len(events[0].payload["elements"]) == 3
        assert events[0].element_id == result["element_id"]

    # Fresh session read-back (persistence + broadcast envelope round trip).
    async with session_scope() as db:
        events = await BoardService().get_events(db, lesson_id)
        assert len(events) == 1
        envelope = events[0].to_envelope()
        assert envelope["type"] == "draw_plan"
        assert envelope["session_id"] == lesson_id


@pytest.mark.asyncio
async def test_board_intent_invalid_model_output_is_rejected(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def bad_director(context):
        return {"elements": [{"id": "x", "kind": "drop_table", "geometry": {}}]}

    monkeypatch.setattr("app.board.intent_handler.call_board_director", bad_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db,
            lesson_id,
            "board_intent",
            {"intent": "Do something unsafe"},
            source="qwen_tool",
        )
    assert result["ok"] is False
    assert result.get("error") in {"invalid_arguments", "intent_failed"}


@pytest.mark.asyncio
async def test_board_intent_validation_retry_then_success(lesson_id, monkeypatch):
    from app.db.session import session_scope

    calls = {"n": 0}

    async def flaky_director(context):
        calls["n"] += 1
        if calls["n"] == 1:
            plan = mock_plan()
            plan["elements"][0]["kind"] = "hatch"  # invalid first attempt
            return plan
        return mock_plan()

    monkeypatch.setattr("app.board.intent_handler.call_board_director", flaky_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Show refraction"}, source="qwen_tool"
        )
    assert result["ok"] is True, result
    assert calls["n"] == 2  # exactly one bounded correction retry


@pytest.mark.asyncio
async def test_board_intent_validation_retry_is_bounded(lesson_id, monkeypatch):
    from app.db.session import session_scope
    from app.board.intent_handler import MAX_VALIDATION_RETRIES

    calls = {"n": 0}

    async def always_bad_director(context):
        calls["n"] += 1
        plan = mock_plan()
        plan["elements"][0]["kind"] = "hatch"
        return plan

    monkeypatch.setattr("app.board.intent_handler.call_board_director", always_bad_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Show refraction"}, source="qwen_tool"
        )
    assert result["ok"] is False
    assert calls["n"] == MAX_VALIDATION_RETRIES + 1


@pytest.mark.asyncio
async def test_board_intent_model_timeout_returns_controlled_error(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def timeout_director(context):
        raise IntentHandlingError("Board director timed out", code="qwen_unavailable", retryable=True)

    monkeypatch.setattr("app.board.intent_handler.call_board_director", timeout_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Show anything"}, source="qwen_tool"
        )
    assert result["ok"] is False
    assert result.get("retryable") is True


@pytest.mark.asyncio
async def test_board_intent_rate_limit_is_controlled(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def rate_limited(context):
        raise IntentHandlingError("rate limited", code="qwen_rate_limited", retryable=True)

    monkeypatch.setattr("app.board.intent_handler.call_board_director", rate_limited)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Show anything"}, source="qwen_tool"
        )
    assert result["ok"] is False
    assert result.get("error") == "qwen_rate_limited"


@pytest.mark.asyncio
async def test_board_intent_registers_triggers_and_focus(lesson_id, monkeypatch):
    from app.db.session import session_scope
    from app.realtime.transcript_triggers import TranscriptTriggerSystem

    system = TranscriptTriggerSystem()
    handler = BoardIntentHandler(board=BoardService(), trigger_system=system)

    async def fake_director(context):
        return mock_plan()

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)
    async with session_scope() as db:
        result = await handler.handle(
            db,
            lesson_id,
            {"intent": "Show refraction", "focus": "new_content", "region_hint": "right"},
        )
    assert result["ok"] is True
    assert result["focus"]["target_ids"] == [result["element_id"]]
    triggers = result["triggers_registered"]
    assert len(triggers) == 3
    assert triggers[0]["phrase"] == "refraction"
    assert triggers[0]["target_id"] == result["element_id"]
    reveals = [trigger for trigger in triggers if trigger["action"] == "reveal"]
    assert [trigger["stage"] for trigger in reveals] == [2, 3]
    assert all(trigger["plan_element_id"] == result["element_id"] for trigger in reveals)
    assert all(trigger["target_ids"] for trigger in reveals)
    assert [trigger["phrase"] for trigger in reveals] == ["incident ray", "labels"]
    assert result["cue_phrases"] == ["incident ray", "labels"]


@pytest.mark.asyncio
async def test_board_intent_session_isolation(lesson_id, seeded_student, make_lesson, monkeypatch):
    from app.db.session import session_scope

    other = await make_lesson(seeded_student, topic="Other Topic", subject="Mathematics")

    async def fake_director(context):
        return mock_plan()

    monkeypatch.setattr("app.board.intent_handler.call_board_director", fake_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        first = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "A"}, source="qwen_tool"
        )
        second = await registry.execute_structured(
            db, other["id"], "board_intent", {"intent": "B"}, source="qwen_tool"
        )
    assert first["ok"] and second["ok"]
    assert first["element_id"] != second["element_id"]
    async with session_scope() as db:
        events_a = await registry.board.get_events(db, lesson_id)
        events_b = await registry.board.get_events(db, other["id"])
    assert len(events_a) == 1 and len(events_b) == 1
    assert events_a[0].session_id == lesson_id
    assert events_b[0].session_id == other["id"]
    assert events_a[0].payload["context"]["topic"] == "Wave Optics"
    assert events_b[0].payload["context"]["topic"] == "Other Topic"


@pytest.mark.asyncio
async def test_board_intent_missing_context_and_empty_board(lesson_id, monkeypatch):
    from app.db.session import session_scope

    async def simple_director(context):
        assert isinstance(context.board_summary, list)
        assert isinstance(context.lesson_state, dict)
        return mock_plan()

    monkeypatch.setattr("app.board.intent_handler.call_board_director", simple_director)
    registry = ToolRegistry(board=BoardService())
    async with session_scope() as db:
        result = await registry.execute_structured(
            db, lesson_id, "board_intent", {"intent": "Show a wave"}, source="qwen_tool"
        )
    assert result["ok"] is True
    assert result["plan"]["element_count"] >= 1


# ---------------------------------------------------------------------------
# director request building (no network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_board_director_requires_api_key(monkeypatch):
    from app.board.intent_handler import IntentContext
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        type(settings),
        "effective_board_director_api_key",
        property(lambda self: ""),
    )
    context = IntentContext(
        board_summary=[],
        lesson_state={},
        transcript=[],
        intent="draw something",
        focus="new_content",
        region_hint=None,
    )
    with pytest.raises(IntentHandlingError) as exc:
        await call_board_director(context)
    assert exc.value.code == "qwen_unavailable"


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
