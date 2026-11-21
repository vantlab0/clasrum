"""Phase 4: generative drawing protocol validation tests (pure, no DB)."""
from __future__ import annotations

import pytest

from app.board.drawing_protocol import (
    DrawingPlanError,
    parse_drawing_plan,
    plan_element_bounds,
    plan_total_duration_ms,
)
from app.board.intent_handler import fit_plan_to_region


def _plan(**overrides) -> dict:
    plan = {
        "protocol": "clasrum.drawing",
        "version": 1,
        "title": "Test structure",
        "elements": [
            {
                "id": "body",
                "kind": "ellipse",
                "geometry": {"center": {"x": 0.5, "y": 0.5}, "rx": 0.2, "ry": 0.15},
                "style": {"stroke_color": "green", "stroke_width": 2.0, "fill_color": "green", "fill_opacity": 0.2},
                "layer": 0,
                "stage": 1,
                "duration_ms": 700,
            },
            {
                "id": "bezier_tail",
                "kind": "cubic_bezier",
                "geometry": {
                    "start": {"x": 0.7, "y": 0.5},
                    "c1": {"x": 0.8, "y": 0.4},
                    "c2": {"x": 0.85, "y": 0.7},
                    "end": {"x": 0.9, "y": 0.6},
                },
                "style": {"stroke_color": "primary", "stroke_width": 1.2, "line_cap": "round", "line_join": "round"},
                "layer": 1,
                "stage": 2,
                "duration_ms": 400,
                "delay_ms": 120,
            },
            {
                "id": "label",
                "kind": "text",
                "geometry": {"text": "nucleus", "position": {"x": 0.45, "y": 0.48}, "align": "center"},
                "layer": 2,
                "stage": 3,
                "duration_ms": 250,
            },
        ],
        "stages": [
            {"stage": 1, "description": "body outline", "element_ids": ["body"], "pause_ms": 150},
            {"stage": 2, "description": "tail", "element_ids": ["bezier_tail"]},
            {"stage": 3, "description": "labels", "element_ids": ["label"]},
        ],
    }
    plan.update(overrides)
    return plan


def test_valid_plan_parses_and_serializes_round_trip():
    parsed = parse_drawing_plan(_plan())
    payload = parsed.to_payload()
    assert payload["elements"][0]["geometry"]["center"] == {"x": 0.5, "y": 0.5}
    assert payload["elements"][1]["geometry"]["c1"] == {"x": 0.8, "y": 0.4}
    assert payload["stages"][2]["element_ids"] == ["label"]
    # Round trip: serialized payload revalidates identically.
    assert parse_drawing_plan(payload).to_payload() == payload


def test_total_duration_follows_sequential_stages_and_delays():
    payload = parse_drawing_plan(_plan()).to_payload()
    # stage1: 700; pause 150; stage2: delay 120 + 400; stage3: 250.
    assert plan_total_duration_ms(payload) == 700 + 150 + 120 + 400 + 250


_GEOMETRY_KINDS: dict[str, dict] = {
    "line": {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.5, "y": 0.5}},
    "polyline": {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.3}, {"x": 0.6, "y": 0.5}]},
    "polygon": {
        "points": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.3, "y": 0.5}],
        "closed": True,
    },
    "rectangle": {"position": {"x": 0.2, "y": 0.2}, "size": {"w": 0.3, "h": 0.2}},
    "ellipse": {"center": {"x": 0.5, "y": 0.5}, "rx": 0.2, "ry": 0.1},
    "arc": {
        "center": {"x": 0.5, "y": 0.5},
        "rx": 0.2,
        "ry": 0.2,
        "start_angle": 0.0,
        "sweep_angle": 1.57,
    },
    "quad_bezier": {
        "start": {"x": 0.1, "y": 0.5},
        "control": {"x": 0.5, "y": 0.1},
        "end": {"x": 0.9, "y": 0.5},
    },
    "cubic_bezier": {
        "start": {"x": 0.1, "y": 0.5},
        "c1": {"x": 0.3, "y": 0.1},
        "c2": {"x": 0.7, "y": 0.9},
        "end": {"x": 0.9, "y": 0.5},
    },
    "path": {
        "segments": [
            {"op": "move", "point": {"x": 0.1, "y": 0.1}},
            {"op": "line", "point": {"x": 0.5, "y": 0.5}},
            {"op": "close"},
        ]
    },
    "text": {"text": "hello", "position": {"x": 0.5, "y": 0.5}},
    "arrow": {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.5, "y": 0.5}},
}


def test_geometry_variants_all_validate():
    plan = _plan()
    for kind, geometry in _GEOMETRY_KINDS.items():
        p = dict(plan)  # shallow copy; the original plan has 3 elements + 3 stages.
        elements = [
            {
                "id": "only",
                "kind": kind,
                "geometry": geometry,
                "stage": 1,
                "duration_ms": 300,
            }
        ]
        if kind == "text":
            # The protocol requires at least one non-text drawable element.
            elements.append(
                {
                    "id": "backdrop",
                    "kind": "line",
                    "geometry": {"start": {"x": 0.1, "y": 0.1}, "end": {"x": 0.5, "y": 0.5}},
                    "stage": 1,
                    "duration_ms": 300,
                }
            )
        p["elements"] = elements
        # Retain original stage description for the single stage that follows.
        p["stages"] = [
            {
                "stage": 1,
                "description": _plan()["stages"][0]["description"],
                "element_ids": ["only"],
            }
        ]
        parsed = parse_drawing_plan(p)
        assert parsed.elements[0].kind == kind


def test_bezier_requires_control_points():
    plan = _plan()
    plan["elements"][1]["geometry"] = {"start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 0}}
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_element_count_cap():
    plan = _plan()
    plan["elements"] = [
        {
            "id": f"e{i}",
            "kind": "line",
            "geometry": {"start": {"x": 0.1 * (i % 10), "y": 0.1}, "end": {"x": 0.2, "y": 0.2}},
            "stage": 1,
            "duration_ms": 100,
        }
        for i in range(121)
    ]
    plan["stages"] = [{"stage": 1, "element_ids": []}]
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_max_path_complexity_cap():
    plan = _plan()
    plan["elements"][0] = {
        "id": "huge",
        "kind": "path",
        "geometry": {
            "segments": [
                {"op": "line", "point": {"x": 0.001 * i, "y": 0.5}} for i in range(1, 170)
            ]
        },
        "stage": 1,
        "duration_ms": 500,
    }
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_invalid_coordinate_rejected():
    plan = _plan()
    plan["elements"][0]["geometry"]["rx"] = 1.5
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)
    plan["elements"][0]["geometry"]["rx"] = -0.1
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_stroke_and_shading_property_bounds():
    plan = _plan()
    plan["elements"][0]["style"]["stroke_width"] = 9.0
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)
    plan["elements"][0]["style"]["stroke_width"] = 2.0
    plan["elements"][0]["style"]["fill_opacity"] = 1.4
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_unsafe_text_and_control_chars_rejected():
    plan = _plan()
    plan["elements"][2]["geometry"]["text"] = "ok\x07bell"
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)
    # text length should be <= MAX_TEXT_LENGTH; these are well within bounds.
    plan["elements"][2]["geometry"]["text"] = "ok control"
    parsed = parse_drawing_plan(plan)
    assert parsed.elements[2].kind == "text"


def test_clip_reference_must_be_earlier_and_closed():
    plan = _plan()
    plan["elements"][1]["clip_id"] = "body"  # valid: body is earlier and closed
    parse_drawing_plan(plan)
    plan["elements"][0]["clip_id"] = "bezier_tail"  # forward reference
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)
    plan["elements"][0]["clip_id"] = None
    plan["elements"].append(
        {
            "id": "clip_open",
            "kind": "line",
            "geometry": {"start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 1}},
            "stage": 1,
            "duration_ms": 200,
        }
    )
    plan["elements"][0]["clip_id"] = "clip_open"  # open element cannot clip
    with pytest.raises(DrawingPlanError):
        parse_drawing_plan(plan)


def test_bounds_estimation_covers_all_geometry():
    payload = parse_drawing_plan(_plan()).to_payload()
    boxes = plan_element_bounds(payload)
    assert len(boxes) == 3
    body, tail, label = boxes
    assert body == (0.3, 0.35, 0.4, 0.3)
    assert tail[0] <= 0.7 and tail[2] >= 0.2
    assert label[2] > 0


def test_fit_plan_to_region_uniform_scale_and_center():
    plan = parse_drawing_plan(_plan()).to_payload()
    original_bounds = plan_element_bounds(plan)
    fitted = fit_plan_to_region(plan, (0.6, 0.1, 0.3, 0.5))
    fitted_bounds = plan_element_bounds(fitted)
    xs = [b[0] for b in fitted_bounds]
    ys = [b[1] for b in fitted_bounds]
    rights = [b[0] + b[2] for b in fitted_bounds]
    bottoms = [b[1] + b[3] for b in fitted_bounds]
    assert min(xs) >= 0.6 - 1e-6
    assert min(ys) >= 0.1 - 1e-6
    assert max(rights) <= 0.9 + 1e-6
    assert max(bottoms) <= 0.6 + 1e-6
    # Aspect ratio preserved.
    original_aspect = (original_bounds[0][2]) / (original_bounds[0][3])
    fitted_aspect = (fitted_bounds[0][2]) / (fitted_bounds[0][3])
    assert fitted_aspect == pytest.approx(original_aspect, rel=0.05)
