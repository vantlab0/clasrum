"""Tests for the single deterministic blackboard layout engine."""
from __future__ import annotations

import pytest

from app.board.layout import (
    BlackboardLayoutManager,
    LayoutConfig,
    LayoutNoSpaceError,
    Rect,
    build_layout_state,
    estimate_equation_size,
    estimate_table_size,
    estimate_text_size,
)


def _overlaps(a: dict, b: dict) -> bool:
    ra = Rect(a["x"], a["y"], a["w"], a["h"])
    rb = Rect(b["x"], b["y"], b["w"], b["h"])
    return ra.intersects(rb)


def _box(element: dict) -> dict:
    """Flatten a placed payload's position/size into one bounds dict."""
    return element["position"] | element["size"]


def _inside(element: dict, config: LayoutConfig) -> bool:
    rect = Rect(element["x"], element["y"], element["w"], element["h"])
    return config.safe.contains(rect)


def _write(manager: BlackboardLayoutManager, style: str, text: str, **extra):
    payload = {
        "style": style,
        "text": text,
        "heading_level": int(style[1:]) if style.startswith("h") and style[1:].isdigit() else 0,
        "position_hint": "next_line",
        "side_preference": "auto",
    }
    payload.update(extra)
    return manager.place_text(payload)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_heading_then_subheading_uses_real_levels_and_vertical_flow():
    manager = BlackboardLayoutManager({"elements": []})
    h1 = _write(manager, "h1", "Photosynthesis")
    h3 = _write(manager, "h3", "Light-dependent reactions")
    assert h1["heading_level"] == 1 and h3["heading_level"] == 3
    assert estimate_text_size("Photosynthesis", style="heading", heading_level=1)[1] > \
        estimate_text_size("Light reactions", style="heading", heading_level=3)[1]
    assert h3["position"]["y"] > h1["position"]["y"]
    assert not _overlaps(_box(h1), _box(h3))


def test_two_consecutive_h1_keep_full_size_and_vertical_spacing():
    manager = BlackboardLayoutManager({"elements": []})
    first = _write(manager, "h1", "Cell Structure")
    second = _write(manager, "h1", "Energy Flow")
    assert second["position"]["y"] > first["position"]["y"] + manager.config.element_gap
    assert not _overlaps(_box(first), _box(second))
    for item, text in ((first, "Cell Structure"), (second, "Energy Flow")):
        expected = estimate_text_size(text, style="heading", heading_level=1)
        assert item["size"]["w"] == pytest.approx(expected[0])
        assert item["size"]["h"] == pytest.approx(expected[1])


def test_heading_subtitle_and_body_keep_distinct_font_heights():
    manager = BlackboardLayoutManager({"elements": []})
    heading = _write(manager, "h2", "Photosynthesis")
    subtitle = _write(manager, "h4", "Light reactions")
    body = _write(manager, "body", "Chlorophyll absorbs light energy.")
    heights = [item["size"]["h"] for item in (heading, subtitle, body)]
    assert heights[0] > heights[1] > heights[2]
    boxes = [_box(item) for item in (heading, subtitle, body)]
    for index in range(1, len(boxes)):
        assert boxes[index]["y"] > boxes[index - 1]["y"]
        assert not _overlaps(boxes[index], boxes[index - 1])


def test_font_scale_changes_reservation_and_never_shrinks_text():
    manager = BlackboardLayoutManager({"elements": []})
    normal = _write(manager, "body", "A carefully measured sentence.")
    enlarged = _write(manager, "body", "A carefully measured sentence.", font_scale=2.0)
    assert enlarged["size"]["h"] > normal["size"]["h"] * 1.8
    assert enlarged["size"]["w"] >= normal["size"]["w"]
    expected = estimate_text_size("A carefully measured sentence.", style="body", font_scale=2.0)
    assert enlarged["size"]["w"] == pytest.approx(expected[0])
    assert enlarged["size"]["h"] == pytest.approx(expected[1])
    assert not _overlaps(_box(normal), _box(enlarged))


def test_sizeless_heading_obstacle_uses_real_heading_footprint():
    heading_w, heading_h = estimate_text_size(
        "Cell Structure", style="heading", heading_level=2
    )
    doc = {"elements": [{
        "element_id": "h2_1",
        "type": "write_text",
        "created_sequence": 1,
        "position": {"x": 0.08, "y": 0.08},
        "payload": {"text": "Cell Structure", "style": "heading", "heading_level": 2},
    }]}
    manager = BlackboardLayoutManager(doc)
    obstacle = Rect(0.08, 0.08, heading_w, heading_h)
    placed = _write(manager, "body", "The nucleus controls the cell.")
    assert not Rect(**_box(placed)).intersects(obstacle)


def test_flow_cursor_continues_across_manager_rebuilds():
    first_manager = BlackboardLayoutManager({"elements": []})
    first = _write(first_manager, "h2", "Overview")
    doc = {"elements": [{
        "element_id": "h2_1",
        "type": "write_text",
        "created_sequence": 1,
        **first,
        "payload": first,
    }]}
    second_manager = BlackboardLayoutManager(doc)
    second = _write(second_manager, "body", "This lesson covers three ideas.")
    assert second["position"]["y"] > first["position"]["y"]


def test_multiple_body_texts_never_overlap():
    manager = BlackboardLayoutManager({"elements": []})
    placed = [_write(manager, "body", f"Definition number {i}: a short teaching sentence.") for i in range(8)]
    for index, current in enumerate(placed):
        box = _box(current)
        assert _inside(box, manager.config)
        for previous in placed[:index]:
            assert not _overlaps(box, _box(previous))


def test_multiple_equations_with_latex_are_placed_and_sized():
    manager = BlackboardLayoutManager({"elements": []})
    equations = ["E = mc^2", "\\frac{a}{b} + \\sqrt{x}", "\\sum_{i=1}^{n} i^2"]
    placed = [_write(manager, "equation", latex, latex=latex) for latex in equations]
    assert all(item["style"] == "equation" for item in placed)
    for index, current in enumerate(placed):
        box = _box(current)
        assert box["w"] >= estimate_equation_size(equations[index])[0] * 0.6
        for previous in placed[:index]:
            assert not _overlaps(box, _box(previous))


def test_side_by_side_respects_side_preference_and_falls_back_to_vertical():
    manager = BlackboardLayoutManager({"elements": []})
    left = _write(manager, "body", "Mass is the amount of matter.")
    right = _write(manager, "body", "Weight is the force of gravity.", side_preference="right")
    right_box = _box(right)
    left_box = _box(left)
    assert right_box["x"] > left_box["x"] or right_box["y"] > left_box["y"]
    assert not _overlaps(left_box, right_box)
    # A narrow board cannot fit two columns: placement still succeeds vertically.
    narrow = BlackboardLayoutManager({"elements": []}, LayoutConfig(max_content_width=0.30))
    a = _write(narrow, "body", "First definition.")
    b = _write(narrow, "body", "Second definition.", side_preference="right")
    assert not _overlaps(_box(a), _box(b))


def test_collision_detection_treats_existing_elements_as_obstacles():
    existing = {"elements": [{
        "element_id": "el_1", "type": "write_text",
        "position": {"x": 0.04, "y": 0.045}, "size": {"w": 0.40, "h": 0.10},
    }]}
    manager = BlackboardLayoutManager(existing)
    state = build_layout_state(existing)
    assert state.collides(Rect(0.05, 0.05, 0.10, 0.05))
    assert not state.collides(Rect(0.60, 0.60, 0.10, 0.05))
    placed = _write(manager, "body", "New content must not land on the old text.")
    assert not _overlaps(_box(placed),
                         existing["elements"][0]["position"] | existing["elements"][0]["size"])


def test_insufficient_space_returns_controlled_failure():
    config = LayoutConfig(margin_x=0.01, margin_y=0.01, min_width=0.30, min_height=0.30,
                          max_content_width=0.98, step_x=0.05, step_y=0.05)
    doc = {"elements": [
        {"element_id": f"el_{i}", "type": "diagram",
         "position": {"x": x, "y": y}, "size": {"w": 0.48, "h": 0.48}}
        for i, (x, y) in enumerate([(0.01, 0.01), (0.51, 0.01), (0.01, 0.51), (0.51, 0.51)])
    ]}
    manager = BlackboardLayoutManager(doc, config)
    result = manager.place_element("diagram", width=0.3, height=0.3)
    assert not result.ok
    assert result.rect is None
    assert result.reason == "no_space"
    # Text placement converts a controlled failure into an explicit exception.
    with pytest.raises(LayoutNoSpaceError):
        manager.place_text({"style": "diagram", "text": "x", "position_hint": "next_line"})


def test_diagram_and_table_placement_with_group_integrity():
    manager = BlackboardLayoutManager({"elements": []})
    diagram = manager.place_element("diagram", width=0.36, height=0.28)
    assert diagram.ok and _inside(diagram.rect.as_payload(), manager.config)
    table_size = estimate_table_size(5, 3)
    table = manager.place_element("table", width=table_size[0], height=table_size[1])
    assert table.ok and not diagram.rect.intersects(table.rect)

    group = manager.place_group([
        {"id": "cell_1", "dx": 0.0, "dy": 0.0, "width": 0.18, "height": 0.06},
        {"id": "cell_2", "dx": 0.19, "dy": 0.0, "width": 0.18, "height": 0.06},
        {"id": "cell_3", "dx": 0.0, "dy": 0.07, "width": 0.37, "height": 0.06},
    ], group_id="table_group", kind="table")
    assert group.ok
    items = group.payload_items
    assert len(items) == 3
    assert abs(items[1]["position"]["x"] - items[0]["position"]["x"] - 0.19) < 0.01
    for item in items:
        assert _inside(_box(item), manager.config)


def test_responsive_normalized_coordinates_adapt_to_smaller_board():
    config = LayoutConfig(width=1.0, height=1.0, max_content_width=0.42)
    manager = BlackboardLayoutManager({"elements": []}, config)
    placed = [_write(manager, "body", f"Mobile line {i} with enough words to wrap gracefully.") for i in range(5)]
    for element in placed:
        box = _box(element)
        assert 0.0 <= box["x"] <= box["x"] + box["w"] <= 1.0
        assert 0.0 <= box["y"] <= box["y"] + box["h"] <= 1.0
        assert box["w"] <= config.max_content_width + 0.2


def test_highlight_targets_existing_id_without_new_region():
    doc = {"elements": [{"element_id": "el_7", "type": "write_text",
                         "position": {"x": 0.1, "y": 0.1}, "size": {"w": 0.2, "h": 0.05}}]}
    manager = BlackboardLayoutManager(doc)
    before = len(manager.state.entries)
    payload = manager.place_payload({"element_id": "el_7", "style": "highlight"}, kind="annotate")
    assert payload["element_id"] == "el_7"


# ---------------------------------------------------------------------------
# Realistic lesson integration
# ---------------------------------------------------------------------------


def test_realistic_lesson_layout_has_no_unrelated_overlaps():
    manager = BlackboardLayoutManager({"elements": []})
    elements: list[dict[str, float]] = []
    elements.append(_write(manager, "h1", "Cell Structure") | {"kind": "h1"})
    for heading in ("Nucleus", "Mitochondria"):
        elements.append(_write(manager, "h2", heading) | {"kind": "h2"})
    for definition in (
        "The nucleus stores genetic material.",
        "Mitochondria produce ATP by respiration.",
        "The membrane controls what enters the cell.",
    ):
        elements.append(_write(manager, "body", definition) | {"kind": "definition"})
    for equation in ("E = mc^2", "a^2 + b^2 = c^2"):
        elements.append(_write(manager, "equation", equation, latex=equation) | {"kind": "equation"})
    table = estimate_table_size(3, 3)
    placed_table = manager.place_element("table", width=table[0], height=table[1])
    assert placed_table.ok
    elements.append({"position": {"x": placed_table.rect.x, "y": placed_table.rect.y},
                     "size": {"w": placed_table.rect.w, "h": placed_table.rect.h},
                     "kind": "table"})
    diagram = manager.place_element("diagram", width=0.36, height=0.28)
    assert diagram.ok
    elements.append({"position": {"x": diagram.rect.x, "y": diagram.rect.y},
                     "size": {"w": diagram.rect.w, "h": diagram.rect.h},
                     "kind": "diagram"})

    for index, element in enumerate(elements):
        assert _inside(_box(element), manager.config)
        for previous in elements[:index]:
            assert not _overlaps(_box(element), _box(previous)), (index, element, previous)


def test_same_group_overlaps_are_allowed_but_other_groups_are_not():
    manager = BlackboardLayoutManager({"elements": []})
    first = manager.place_element("node", width=0.2, height=0.1, group_id="diagram_1")
    second = manager.place_element("node", width=0.2, height=0.1, group_id="diagram_1")
    assert first.ok and second.ok
    # Same-group reservations are exempt from collision checks by design.
    assert manager.state.collides(Rect(0.04, 0.045, 0.05, 0.05), group_id=None)
    assert not manager.state.collides(Rect(0.04, 0.045, 0.05, 0.05), group_id="diagram_1")
    assert manager.state.collides(Rect(0.04, 0.045, 0.05, 0.05), group_id="other_group")
