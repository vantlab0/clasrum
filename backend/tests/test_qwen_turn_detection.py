"""Semantic VAD stays disabled; Qwen runs in manual/push-to-talk mode."""
from __future__ import annotations

from app.realtime.qwen_adapter import build_turn_detection


def test_default_and_semantic_aliases_disable_server_turn_detection() -> None:
    for configured in ("manual", "", "none", "off", "disabled", "push_to_talk"):
        assert build_turn_detection(configured, 500) is None, configured


def test_semantic_vad_setting_can_be_explicitly_opted_into_but_is_not_the_default() -> None:
    assert build_turn_detection("semantic_vad_ec", 500) == {
        "type": "semantic_vad_ec"
    }
    assert build_turn_detection("manual", 500) != {"type": "semantic_vad_ec"}


def test_server_vad_keeps_threshold_and_silence() -> None:
    assert build_turn_detection("server_vad", 750) == {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 750,
    }


def test_unknown_values_fail_closed_to_manual_mode() -> None:
    assert build_turn_detection("typo", 500) is None
