"""Regression: dark/gray illustration ink can never reach the black board."""
from __future__ import annotations

import pytest

from app.board.drawing_protocol import DrawingElement, ProtocolStyle
from app.board.palette import (
    ILLUSTRATION_COLORS,
    contrast_ratio_against_board,
    is_approved_illustration_color,
    sanitize_illustration_color,
)
from app.board.schemas import DataPoint, DrawDiagramArgs, DrawLineArgs


@pytest.mark.parametrize("value", ["#000000", "#111111", "#333333", "#808080", "#F2F0E8", "#A8A79E", "black", "grey", "offwhite", "  "])
def test_untrusted_dark_or_unknown_colours_are_snapped(value: str) -> None:
    sanitized = sanitize_illustration_color(value)
    assert is_approved_illustration_color(sanitized)
    assert sanitized in ILLUSTRATION_COLORS.values()
    assert contrast_ratio_against_board(sanitized) > 3.0


def test_every_approved_illustration_colour_is_bright_and_distinct() -> None:
    values = set(ILLUSTRATION_COLORS.values())
    assert len(values) == len(ILLUSTRATION_COLORS) == 9
    assert all(is_approved_illustration_color(value) for value in values)
    assert all(contrast_ratio_against_board(value) > 3.0 for value in values)


def test_tool_boundaries_sanitize_illustration_colours() -> None:
    line = DrawLineArgs.model_validate(
        {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.8, "y": 0.8}, "color": "#101010"}
    ).build_payload()
    assert is_approved_illustration_color(line["color"])

    point = DataPoint.model_validate({"x": 0.4, "y": 0.6, "color": "#222222"}).to_payload()
    assert is_approved_illustration_color(point["color"])

    diagram = DrawDiagramArgs.model_validate(
        {
            "kind": "flowchart",
            "nodes": [
                {"id": "a", "position": {"x": 0.1, "y": 0.1}, "color": "gray"},
                {"id": "b", "position": {"x": 0.7, "y": 0.1}, "color": "#000000"},
            ],
            "edges": [{"from": "a", "to": "b", "color": "#333333"}],
            "color": "black",
        }
    ).build_payload()
    assert is_approved_illustration_color(diagram["color"])
    assert is_approved_illustration_color(diagram["edges"][0]["color"])
    assert {node["color"] for node in diagram["nodes"]} <= set(ILLUSTRATION_COLORS.values())


def test_generated_plan_style_sanitizes_dark_colours() -> None:
    style = ProtocolStyle.model_validate(
        {"stroke_color": "#050505", "stroke_width": 2.0}
    )
    assert is_approved_illustration_color(style.stroke_color)
    assert is_approved_illustration_color(
        ProtocolStyle.model_validate({"fill_color": "gray"}).fill_color
    )

    element = DrawingElement.model_validate(
        {
            "id": "l1",
            "kind": "line",
            "geometry": {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.8, "y": 0.8}},
            "style": style.model_dump(),
        }
    )
    assert is_approved_illustration_color(element.style.stroke_color)
