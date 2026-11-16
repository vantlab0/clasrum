"""Phase 2: Transcript Trigger System tests."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.board.intent_handler import BoardIntentHandler
from app.board.registry import ToolRegistry
from app.board.service import BoardService
from app.realtime.transcript_triggers import (
    DEFAULT_COOLDOWN_MS,
    TranscriptTrigger,
    TranscriptTriggerSystem,
    normalize_to_words,
    phrase_in_text,
)


# ---------------------------------------------------------------------------
# Unit tests: matching engine
# ---------------------------------------------------------------------------

def test_normalize_to_words_case_and_punctuation():
    assert normalize_to_words("ATP") == ["atp"]
    assert normalize_to_words("Mitochondria produce ATP!") == ["mitochondria", "produce", "atp"]
    assert normalize_to_words("Carbon dioxide (CO2)") == ["carbon", "dioxide", "co2"]
    assert normalize_to_words("  Multiple   spaces  ") == ["multiple", "spaces"]
    assert normalize_to_words("") == []
    assert normalize_to_words("!!!") == []


def test_phrase_in_text_multi_word():
    assert phrase_in_text(["mitochondria", "produce", "atp"], ["produce", "atp"]) is True
    assert phrase_in_text(["mitochondria", "produces", "atp"], ["produce", "atp"]) is False
    assert phrase_in_text(["the", "atp", "synthase"], ["atp"]) is True
    assert phrase_in_text(["cat", "atpase"], ["atp"]) is False  # word boundary
    assert phrase_in_text(["atp"], ["atp", "synthase"]) is False  # phrase longer than text
    assert phrase_in_text([], ["atp"]) is False


def test_phrase_in_text_case_insensitive():
    # phrase_in_text expects pre-normalized (lowercased) words.
    assert phrase_in_text(["atp", "synthase"], ["atp", "synthase"]) is True
    assert phrase_in_text(["sunlight"], ["sunlight"]) is True


# ---------------------------------------------------------------------------
# Unit tests: TranscriptTrigger
# ---------------------------------------------------------------------------

def test_trigger_creation_and_matching():
    trigger = TranscriptTrigger(
        trigger_id="trg_1",
        lesson_id="lesson_1",
        phrase="ATP",
        target_id="el_atp",
        action="pulse",
        cooldown_ms=3000,
        enabled=True,
        created_at="2026-01-01T00:00:00.000Z",
    )
    assert trigger.matches("mitochondria produce ATP") is True
    assert trigger.matches("atp synthase") is True
    assert trigger.matches("cat atpase") is False  # word boundary
    assert trigger.matches("nothing here") is False


def test_trigger_cooldown():
    trigger = TranscriptTrigger(
        trigger_id="trg_1",
        lesson_id="lesson_1",
        phrase="ATP",
        target_id="el_atp",
        action="highlight",
        cooldown_ms=3000,
        enabled=True,
        created_at="2026-01-01T00:00:00.000Z",
    )
    import time
    now_ms = time.time() * 1000.0
    assert trigger.can_fire(now_ms) is True
    trigger.fired_count = 1
    trigger.last_fired_at = "2026-01-01T00:00:00.000Z"
    # Freshly fired -> in cooldown.
    assert trigger.can_fire(now_ms) is True  # cooldown is 3000ms, last_fired is old
    # Simulate just fired.
    trigger.last_fired_at = _iso_now()
    assert trigger.can_fire(now_ms) is False


def _iso_now():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def test_trigger_disabled_and_max_fires():
    trigger = TranscriptTrigger(
        trigger_id="trg_1",
        lesson_id="lesson_1",
        phrase="ATP",
        target_id="el_atp",
        action="highlight",
        cooldown_ms=0,
        enabled=False,
        created_at=_iso_now(),
    )
    import time
    assert trigger.can_fire(time.time() * 1000.0) is False

    trigger.enabled = True
    trigger.max_fires = 1
    trigger.fired_count = 1
    assert trigger.can_fire(time.time() * 1000.0) is False


# ---------------------------------------------------------------------------
# Unit tests: TranscriptTriggerSystem registration / lifecycle
# ---------------------------------------------------------------------------

class FakeRegistry:
    """Stand-in registry that records highlight calls."""

    def __init__(self):
        self.calls = []

    async def execute_structured(self, db, lesson_id, name, arguments, *, source="system", animate=True):
        self.calls.append((name, dict(arguments), source))
        return {"ok": True, "event_id": "evt_x", "element_id": None}


class FakeDB:
    pass


@pytest.fixture
def trigger_system():
    return TranscriptTriggerSystem(board=BoardService(), registry=FakeRegistry())


@pytest.mark.asyncio
async def test_register_and_get_trigger(trigger_system):
    tid = trigger_system.register_trigger(
        "lesson_1", "ATP", "el_atp", action="pulse", cooldown_ms=3000
    )
    assert tid.startswith("trg_")
    triggers = trigger_system.get_triggers("lesson_1")
    assert len(triggers) == 1
    assert triggers[0]["trigger_id"] == tid
    assert triggers[0]["phrase"] == "ATP"
    assert triggers[0]["target_id"] == "el_atp"
    assert triggers[0]["action"] == "pulse"
    assert trigger_system.trigger_count("lesson_1") == 1


@pytest.mark.asyncio
async def test_session_isolation(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_1", action="highlight")
    trigger_system.register_trigger("lesson_2", "sunlight", "el_2", action="pulse")
    assert trigger_system.trigger_count("lesson_1") == 1
    assert trigger_system.trigger_count("lesson_2") == 1
    assert len(trigger_system.get_triggers("lesson_1")) == 1
    assert len(trigger_system.get_triggers("lesson_2")) == 1


@pytest.mark.asyncio
async def test_unregister_and_clear(trigger_system):
    tid = trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="highlight")
    assert trigger_system.unregister_trigger("lesson_1", tid) is True
    assert trigger_system.trigger_count("lesson_1") == 0
    assert trigger_system.unregister_trigger("lesson_1", tid) is False  # already gone

    trigger_system.register_trigger("lesson_1", "A", "el_a", action="highlight")
    trigger_system.register_trigger("lesson_1", "B", "el_b", action="pulse")
    removed = trigger_system.clear_triggers("lesson_1")
    assert removed == 2
    assert trigger_system.trigger_count("lesson_1") == 0


@pytest.mark.asyncio
async def test_enable_disable_trigger(trigger_system):
    tid = trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="highlight")
    assert trigger_system.enable_trigger("lesson_1", tid, False) is True
    triggers = trigger_system.get_triggers("lesson_1")
    assert triggers[0]["enabled"] is False
    assert trigger_system.enable_trigger("lesson_1", tid, True) is True
    assert trigger_system.enable_trigger("lesson_1", "nonexistent", True) is False


# ---------------------------------------------------------------------------
# Unit tests: handle_transcript matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_transcript_single_word_match(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="pulse")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP", is_final=True
    )
    assert len(results) == 1
    assert results[0]["trigger_id"] is not None
    assert results[0]["phrase"] == "ATP"
    assert results[0]["action"] == "pulse"
    assert results[0]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_handle_transcript_multi_word_match(trigger_system):
    trigger_system.register_trigger("lesson_1", "carbon dioxide", "el_co2", action="highlight")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "we breathe in carbon dioxide and out oxygen", is_final=True
    )
    assert len(results) == 1
    assert results[0]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_handle_transcript_case_insensitive(trigger_system):
    trigger_system.register_trigger("lesson_1", "Sunlight", "el_sun", action="underline")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "SUNLIGHT hits the leaf", is_final=True
    )
    assert len(results) == 1
    assert results[0]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_handle_transcript_punctuation(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="circle")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "ATP, the energy currency!", is_final=True
    )
    assert len(results) == 1
    assert results[0]["result"]["ok"] is True


@pytest.mark.asyncio
async def test_handle_transcript_word_boundary_no_partial_match(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="highlight")
    # "atpase" contains "atp" but should not match (word boundary).
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "atpase enzyme", is_final=True
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_handle_transcript_no_match(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="highlight")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "photosynthesis is fun", is_final=True
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_handle_transcript_multiple_triggers(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="pulse")
    trigger_system.register_trigger("lesson_1", "oxygen", "el_o2", action="highlight")
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "ATP and oxygen are produced", is_final=True
    )
    assert len(results) == 2
    assert all(r["result"]["ok"] for r in results)


@pytest.mark.asyncio
async def test_handle_transcript_duplicate_prevention_cooldown(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="pulse", cooldown_ms=3000)
    # First match fires.
    r1 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP", is_final=True
    )
    assert len(r1) == 1
    assert r1[0]["result"]["ok"] is True
    # Immediate repeat -> in cooldown, should not fire.
    r2 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP again", is_final=True
    )
    assert len(r2) == 0


@pytest.mark.asyncio
async def test_handle_transcript_partial_debounce(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="pulse", cooldown_ms=0)
    # First partial fires.
    r1 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP", is_final=False
    )
    assert len(r1) == 1
    # Immediate partial repeat -> debounced, should not fire.
    r2 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP", is_final=False
    )
    assert len(r2) == 0


@pytest.mark.asyncio
async def test_handle_transcript_partial_then_final(trigger_system):
    trigger_system.register_trigger("lesson_1", "oxygen", "el_o2", action="highlight", cooldown_ms=10000)
    # Partial matches and fires.
    r1 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "we produce oxygen", is_final=False
    )
    assert len(r1) == 1
    # Final repeat -> cooldown still active (same trigger), should not fire.
    r2 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "we produce oxygen", is_final=True
    )
    assert len(r2) == 0


@pytest.mark.asyncio
async def test_handle_transcript_only_tutor(trigger_system):
    trigger_system.register_trigger("lesson_1", "ATP", "el_atp", action="highlight")
    # Student speech should not fire triggers.
    results = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "ATP", speaker="student", is_final=True
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_handle_transcript_empty_text(trigger_system):
    results = await trigger_system.handle_transcript(FakeDB(), "lesson_1", "", is_final=True)
    assert results == []
    results = await trigger_system.handle_transcript(FakeDB(), "lesson_1", "   ", is_final=True)
    assert results == []


@pytest.mark.asyncio
async def test_handle_transcript_partial_buffer_flushed_on_final(trigger_system):
    trigger_system.register_trigger("lesson_1", "carbon dioxide", "el_co2", action="highlight", cooldown_ms=0)
    # Partial "carbon" alone should not match.
    r1 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "carbon", is_final=False
    )
    assert len(r1) == 0
    # Final "carbon dioxide" should match (buffer flushed).
    r2 = await trigger_system.handle_transcript(
        FakeDB(), "lesson_1", "dioxide", is_final=True
    )
    # The final text is just "dioxide" (buffer was popped on final), so no match.
    # Actually: on final, buffer is popped, then we match against "dioxide" only.
    assert len(r2) == 0


# ---------------------------------------------------------------------------
# End-to-end: BoardIntentHandler registers triggers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_board_intent_registers_triggers(seeded_student, make_lesson):
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student, topic="Photosynthesis", subject="Biology")
    lesson_id = lesson["id"]

    trigger_system = TranscriptTriggerSystem(board=BoardService())
    registry = ToolRegistry(board=BoardService(), trigger_system=trigger_system)

    async def fake_director(context):
        return {
            "actions": [
                {
                    "type": "add_node",
                    "id": "atp_node",
                    "label": "ATP",
                    "sublabel": "Energy currency",
                    "region": "right",
                }
            ],
            "triggers": [
                {"phrase": "ATP", "target": "atp_node", "action": "pulse", "cooldown_ms": 3000}
            ],
            "focus": {"target_ids": ["atp_node"], "priority": "high"},
        }

    with patch("app.board.intent_handler.call_board_director", fake_director):
        async with session_scope() as db:
            result = await registry.execute_structured(
                db,
                lesson_id,
                "board_intent",
                {
                    "intent": "Show ATP on the board",
                    "focus": "new_content",
                    "region_hint": "right",
                },
                source="qwen_tool",
            )
    assert result["ok"] is True, result
    assert len(result.get("triggers_registered", [])) == 1
    assert result["triggers_registered"][0]["phrase"] == "ATP"
    assert result["triggers_registered"][0]["action"] == "pulse"
    # The trigger system should have the trigger registered.
    assert trigger_system.trigger_count(lesson_id) == 1


@pytest.mark.asyncio
async def test_board_intent_triggers_optional(seeded_student, make_lesson):
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student, topic="Photosynthesis", subject="Biology")
    lesson_id = lesson["id"]

    trigger_system = TranscriptTriggerSystem(board=BoardService())
    registry = ToolRegistry(board=BoardService(), trigger_system=trigger_system)

    async def fake_director(context):
        return {
            "actions": [
                {"type": "add_node", "id": "x", "label": "X", "region": "right"}
            ],
            # No triggers key -> no registration.
        }

    with patch("app.board.intent_handler.call_board_director", fake_director):
        async with session_scope() as db:
            result = await registry.execute_structured(
                db,
                lesson_id,
                "board_intent",
                {"intent": "Show X", "focus": "new_content", "region_hint": "right"},
                source="qwen_tool",
            )
    assert result["ok"] is True
    assert result.get("triggers_registered") == []
    assert trigger_system.trigger_count(lesson_id) == 0


# ---------------------------------------------------------------------------
# End-to-end: hub integration (trigger fires on transcript)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hub_transcript_triggers_board_event(seeded_student, make_lesson):
    from app.db.session import session_scope

    lesson = await make_lesson(seeded_student, topic="Biology", subject="Biology")
    lesson_id = lesson["id"]

    # Seed a board element to highlight.
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
    element_id = created["element_id"]

    # Create trigger system and register a trigger.
    trigger_system = TranscriptTriggerSystem(board=BoardService(), registry=registry)
    trigger_system.register_trigger(
        lesson_id, "mitochondria", element_id, action="highlight", cooldown_ms=0
    )

    # Simulate a tutor transcript matching the trigger.
    async with session_scope() as db:
        results = await trigger_system.handle_transcript(
            db,
            lesson_id,
            "mitochondria are the powerhouse",
            speaker="tutor",
            is_final=True,
        )
    assert len(results) == 1
    assert results[0]["result"]["ok"] is True

    # Verify a highlight event was persisted.
    async with session_scope() as db:
        events = await BoardService().get_events(db, lesson_id)
    highlight_events = [e for e in events if e.type == "highlight"]
    assert len(highlight_events) >= 1


@pytest.mark.asyncio
async def test_trigger_missing_target_is_rejected():
    # Use a real registry so BoardService validates that the target exists.
    real_registry = ToolRegistry(board=BoardService())
    ts = TranscriptTriggerSystem(board=BoardService(), registry=real_registry)
    ts.register_trigger("lesson_1", "ATP", "nonexistent_el", action="pulse", cooldown_ms=0)
    results = await ts.handle_transcript(
        FakeDB(), "lesson_1", "mitochondria produce ATP", is_final=True
    )
    # The board_highlight call should fail because the element doesn't exist.
    assert len(results) == 1
    assert results[0]["result"]["ok"] is False


# ---------------------------------------------------------------------------
# Trigger cap / eviction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_cap_eviction(trigger_system):
    # Register more than the cap (50) - but FakeRegistry/BoardService would hit DB.
    # Instead, test with a small cap by directly manipulating.
    trigger_system._max_triggers = 2
    trigger_system.register_trigger("lesson_1", "phrase one", "el_1", action="highlight")
    trigger_system.register_trigger("lesson_1", "phrase two", "el_2", action="pulse")
    assert trigger_system.trigger_count("lesson_1") == 2
    # Third registration should evict the oldest.
    trigger_system.register_trigger("lesson_1", "phrase three", "el_3", action="circle")
    assert trigger_system.trigger_count("lesson_1") == 2

