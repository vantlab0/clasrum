"""Deterministic, lightweight region-based layout engine for board intents.

The text model describes *what* to place; this module decides *where* in the
normalized board coordinate space (``[0, 1]`` for both axes, origin top-left).

Algorithm (by design, no force-directed / heavy dependencies):
* divide the board into named logical regions with preferred rects;
* resolve a target region from explicit hints, ``relative_to`` anchors, or by
  picking the least-occupied region;
* place each action's element inside the region on a simple grid cursor, nudging
  it to the first non-colliding slot when an overlap is detected;
* never move or mutate existing elements – only positions assigned to the new
  actions are adjusted.

All public helpers are pure (no I/O, no DB).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

# Normalized board bounds.
BOARD_W = 1.0
BOARD_H = 1.0
EPS = 1e-6

# Preferred rects for named regions: (x, y, w, h).
REGION_RECTS: dict[str, tuple[float, float, float, float]] = {
    "left": (0.04, 0.10, 0.44, 0.82),
    "right": (0.52, 0.10, 0.44, 0.82),
    "top": (0.06, 0.06, 0.88, 0.30),
    "bottom": (0.06, 0.64, 0.88, 0.30),
    "center": (0.24, 0.22, 0.52, 0.56),
    "bottom_left": (0.04, 0.60, 0.44, 0.34),
    "bottom_right": (0.52, 0.60, 0.44, 0.34),
    "top_left": (0.04, 0.06, 0.44, 0.34),
    "top_right": (0.52, 0.06, 0.44, 0.34),
    "full": (0.04, 0.08, 0.92, 0.86),
}

DEFAULT_REGION = "right"
FALLBACK_ORDER = (
    "right",
    "left",
    "bottom",
    "top",
    "center",
    "bottom_right",
    "bottom_left",
    "top_right",
    "top_left",
)

# Default element footprint (w, h) by logical action kind.
DEFAULT_SIZES: dict[str, tuple[float, float]] = {
    "heading": (0.40, 0.08),
    "text": (0.38, 0.10),
    "definition": (0.40, 0.12),
    "bullet_list": (0.36, 0.18),
    "equation": (0.30, 0.08),
    "diagram": (0.36, 0.28),
    "coordinate_system": (0.34, 0.28),
    "graph": (0.34, 0.28),
    "plot": (0.34, 0.28),
    "label": (0.18, 0.05),
    "shape": (0.20, 0.14),
    "arrow": (0.20, 0.10),
    "node": (0.18, 0.08),
    "annotation": (0.18, 0.06),
}
GRID_STEP_X = 0.04
GRID_STEP_Y = 0.05
MAX_PLACEMENT_TRIES = 80


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def clamp_to_board(self) -> "Rect":
        w = min(max(self.w, 0.02), BOARD_W)
        h = min(max(self.h, 0.02), BOARD_H)
        x = max(0.0, min(self.x, BOARD_W - w))
        y = max(0.0, min(self.y, BOARD_H - h))
        return Rect(x=_r(x), y=_r(y), w=_r(w), h=_r(h))

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x2 <= other.x + EPS
            or other.x2 <= self.x + EPS
            or self.y2 <= other.y + EPS
            or other.y2 <= self.y + EPS
        )

    def as_payload(self) -> dict[str, float]:
        return {"x": _r(self.x), "y": _r(self.y), "w": _r(self.w), "h": _r(self.h)}


@dataclass
class LayoutState:
    """Mutable placement state for one planning pass."""

    occupied: list[Rect] = field(default_factory=list)
    region_cursors: dict[str, tuple[float, float]] = field(default_factory=dict)

    def add(self, rect: Rect) -> None:
        self.occupied.append(rect)

    def collides(self, rect: Rect) -> bool:
        return any(rect.intersects(existing) for existing in self.occupied)


def _r(value: float) -> float:
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0 else rounded


def rect_from_element(element: Mapping[str, Any]) -> Rect | None:
    """Best-effort bounds for an existing board element."""
    if element.get("removed_at_sequence") is not None:
        return None
    position = element.get("position") or (element.get("payload") or {}).get("position") or {}
    size = element.get("size") or (element.get("payload") or {}).get("size") or {}
    if isinstance(position, dict) and "x" in position and "y" in position:
        try:
            x = float(position["x"])
            y = float(position["y"])
        except (TypeError, ValueError):
            return None
        w = 0.18
        h = 0.08
        if isinstance(size, dict):
            try:
                if "w" in size:
                    w = float(size["w"])
                if "h" in size:
                    h = float(size["h"])
            except (TypeError, ValueError):
                pass
        else:
            # Heuristic by element type.
            etype = str(element.get("type") or "")
            if "equation" in etype:
                w, h = 0.30, 0.08
            elif "heading" in etype:
                w, h = 0.40, 0.08
            elif "bullet" in etype:
                w, h = 0.36, 0.18
            elif "coordinate" in etype or "graph" in etype or "plot" in etype:
                w, h = 0.34, 0.28
            elif "diagram" in etype:
                w, h = 0.36, 0.28
            elif "definition" in etype:
                w, h = 0.40, 0.12
        return Rect(x, y, max(w, 0.02), max(h, 0.02)).clamp_to_board()

    # Fall back to point clouds (lines/paths/arrows).
    payload = element.get("payload") or {}
    points = payload.get("points") or []
    if not points and isinstance(payload.get("start"), dict) and isinstance(payload.get("end"), dict):
        points = [payload["start"], payload["end"]]
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            xs.append(float(point["x"]))
            ys.append(float(point["y"]))
        except (TypeError, ValueError, KeyError):
            continue
    if xs and ys:
        pad = 0.01
        return Rect(
            min(xs) - pad,
            min(ys) - pad,
            max(xs) - min(xs) + pad * 2,
            max(ys) - min(ys) + pad * 2,
        ).clamp_to_board()
    return None


def build_layout_state(document: Mapping[str, Any] | None) -> LayoutState:
    state = LayoutState()
    if not document:
        return state
    for element in document.get("elements", []) or []:
        rect = rect_from_element(element)
        if rect is not None:
            state.add(rect)
    return state


def region_rect(name: str | None) -> Rect:
    rect = REGION_RECTS.get(str(name or DEFAULT_REGION)) or REGION_RECTS[DEFAULT_REGION]
    return Rect(*rect)


def region_occupancy(state: LayoutState, name: str) -> float:
    target = region_rect(name)
    if target.w <= 0 or target.h <= 0:
        return 0.0
    area = target.w * target.h
    overlap = 0.0
    for occ in state.occupied:
        ix = max(0.0, min(target.x2, occ.x2) - max(target.x, occ.x))
        iy = max(0.0, min(target.y2, occ.y2) - max(target.y, occ.y))
        overlap += ix * iy
    return overlap / area


def choose_region(
    hint: str | None,
    state: LayoutState,
    *,
    relative_to: str | None = None,
    document: Mapping[str, Any] | None = None,
) -> str:
    if relative_to and document:
        # Place near the referenced element's region.
        for element in document.get("elements", []) or []:
            if element.get("element_id") == relative_to and element.get("removed_at_sequence") is None:
                rect = rect_from_element(element)
                if rect is not None:
                    cx = rect.x + rect.w / 2
                    cy = rect.y + rect.h / 2
                    # Prefer to the right of the anchor, else below, else least occupied.
                    if cx < 0.55:
                        return "right"
                    if cy < 0.55:
                        return "bottom"
                    return "left"
    if hint and hint in REGION_RECTS:
        return hint
    # Pick the least occupied region from the fallback order.
    best = DEFAULT_REGION
    best_score = math.inf
    for name in FALLBACK_ORDER:
        score = region_occupancy(state, name)
        if score < best_score - 1e-9:
            best = name
            best_score = score
            if best_score <= 0.0:
                break
    return best


# Heading typography scale factors (relative to body) used for size estimation.
HEADING_SCALE: dict[int, float] = {
    1: 2.0,
    2: 1.65,
    3: 1.4,
    4: 1.2,
    5: 1.05,
    6: 0.95,
}
BODY_FONT_NORM_H = 0.032  # normalized board-height fraction, matches Flutter BoardTextMetrics


def estimate_text_size(
    text: str,
    *,
    style: str = "body",
    heading_level: int | None = None,
    font_scale: float = 1.0,
    max_width: float = 0.72,
) -> tuple[float, float]:
    """Estimate normalized (w, h) for a text element.

    Deterministic heuristic: no Flutter dependency, conservative enough to avoid
    overlap. Uses character count, heading scale, and wrapping against max_width.
    """
    safe_text = " ".join(str(text or "").split())
    if not safe_text:
        return (0.12, 0.04)
    style_key = (style or "body").strip().lower()
    level = int(heading_level or 0)
    if level <= 0 and style_key in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(style_key[1:])
    scale = HEADING_SCALE.get(level, 1.0) if level > 0 else 1.0
    if style_key in {"small", "caption", "note"}:
        scale *= 0.85
    if style_key == "label":
        scale *= 0.8
    font_h = BODY_FONT_NORM_H * scale * max(0.5, min(2.5, float(font_scale or 1.0)))
    # Approx glyph width as fraction of font height (Patrick Hand-ish).
    glyph_w = font_h * 0.52
    usable_w = max(0.12, min(0.92, float(max_width or 0.72)))
    natural_w = max(glyph_w * 2, len(safe_text) * glyph_w)
    width = min(natural_w, usable_w)
    lines = max(1, int(math.ceil(natural_w / max(usable_w, 1e-6))))
    # Extra line for very long wrapped text; cap for sanity.
    lines = min(lines, 12)
    line_h = font_h * 1.35
    height = lines * line_h + font_h * 0.35
    # Clamp to board-safe bounds.
    width = max(0.08, min(0.92, width))
    height = max(0.035, min(0.60, height))
    return (_r(width), _r(height))


def estimate_equation_size(latex: str, *, font_scale: float = 1.0) -> tuple[float, float]:
    text = str(latex or "").strip()
    # Rough heuristic: equations are wider per char and taller.
    char_w = 0.018 * max(0.5, min(2.5, float(font_scale or 1.0)))
    width = max(0.16, min(0.82, len(text) * char_w + 0.04))
    height = max(0.07, min(0.22, 0.075 * max(0.5, min(2.5, float(font_scale or 1.0)))))
    if "\\frac" in text or "\\sum" in text or "\\int" in text:
        height = min(0.26, height * 1.5)
    return (_r(width), _r(height))


def _default_size(action: Mapping[str, Any]) -> tuple[float, float]:
    kind = str(action.get("kind") or action.get("type") or "text").lower()
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else action

    # Direct text payloads (write_text event payload shape).
    style = str((payload or {}).get("style") or "").lower()
    text = str((payload or {}).get("text") or (payload or {}).get("latex") or "")
    heading_level = (payload or {}).get("heading_level")
    font_scale = float((payload or {}).get("font_scale") or 1.0)

    if kind in {"write_equation", "equation"} or style == "equation":
        return estimate_equation_size(text, font_scale=font_scale)
    if kind in {"write_text", "write_heading", "write_definition", "add_text", "text"} or style in {
        "heading", "subheading", "body", "label", "small", "definition", "caption", "note", "code",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }:
        max_width = float((payload or {}).get("max_width") or 0.72)
        if style == "label":
            max_width = min(max_width, 0.36)
        return estimate_text_size(text, style=style, heading_level=heading_level, font_scale=font_scale, max_width=max_width)

    mapping = {
        "add_node": "node",
        "write_bullet_list": "bullet_list",
        "draw_diagram": "diagram",
        "create_coordinate_system": "coordinate_system",
        "plot_function": "plot",
        "plot_points": "plot",
        "add_graph_label": "label",
        "annotate": "annotation",
        "draw_arrow": "arrow",
        "draw_shape": "shape",
    }
    key = mapping.get(kind, kind)
    return DEFAULT_SIZES.get(key, (0.30, 0.10))


def _normalize_region_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    h = str(hint).strip().lower()
    aliases = {
        "next_line": None,
        "left": "left",
        "right": "right",
        "center": "center",
        "top": "top",
        "bottom": "bottom",
        "auto": None,
    }
    if h in REGION_RECTS:
        return h
    return aliases.get(h)


def _side_to_region(side_preference: str | None) -> str | None:
    side = str(side_preference or "auto").strip().lower()
    if side == "left":
        return "left"
    if side == "right":
        return "right"
    return None


def _next_position(region: str, state: LayoutState, w: float, h: float) -> Rect:
    area = region_rect(region)
    cursor = state.region_cursors.get(region)
    if cursor is None:
        cursor = (area.x, area.y)
    x, y = cursor

    for _ in range(MAX_PLACEMENT_TRIES):
        candidate = Rect(x, y, w, h).clamp_to_board()
        # Keep inside region when possible.
        if candidate.x + candidate.w > area.x2 + EPS or candidate.y + candidate.h > area.y2 + EPS:
            x = area.x
            y += GRID_STEP_Y
            if y + h > area.y2 + EPS:
                y = area.y
                x = area.x
            candidate = Rect(x, y, w, h).clamp_to_board()
        if not state.collides(candidate):
            state.region_cursors[region] = (x, y + candidate.h + 0.02)
            return candidate
        x += GRID_STEP_X
        if x + w > area.x2 + EPS:
            x = area.x
            y += GRID_STEP_Y
        if y + h > area.y2 + EPS:
            y = area.y
            x = area.x
    # Last-resort scan over full board to avoid silent overlap.
    scan_y = 0.06
    while scan_y + h <= 0.96 + EPS:
        scan_x = 0.04
        while scan_x + w <= 0.96 + EPS:
            candidate = Rect(scan_x, scan_y, w, h).clamp_to_board()
            if not state.collides(candidate):
                state.region_cursors[region] = (candidate.x, candidate.y + candidate.h + 0.02)
                return candidate
            scan_x += GRID_STEP_X
        scan_y += GRID_STEP_Y
    return Rect(area.x, area.y, w, h).clamp_to_board()


def place_action(
    action: Mapping[str, Any],
    state: LayoutState,
    *,
    document: Mapping[str, Any] | None = None,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *action* with a normalized ``position`` (and ``size``).

    Respects semantic hints (``position_hint``, ``side_preference``) and avoids
    collisions against occupied regions. Explicit numeric positions are clamped
    and collision-checked; if they collide, we relocate.
    """
    result = dict(action)
    action_type = str(action.get("type") or "")
    if action_type in {"annotate", "highlight", "focus", "erase", "clear"}:
        return result

    payload = result.get("payload") if isinstance(result.get("payload"), dict) else result
    hint = (
        result.get("region")
        or (payload or {}).get("position_hint")
        or (payload or {}).get("region")
        or default_region
    )
    side_pref = (payload or {}).get("side_preference") or result.get("side_preference")
    region_hint = _normalize_region_hint(str(hint) if hint else None) or _side_to_region(side_pref)

    # Determine size first (used for explicit and auto placement).
    w, h = _default_size(payload if isinstance(payload, dict) else result)
    size = result.get("size") if isinstance(result.get("size"), dict) else (payload or {}).get("size")
    if isinstance(size, dict):
        try:
            w = max(0.02, min(0.96, float(size.get("w", w))))
            h = max(0.02, min(0.96, float(size.get("h", h))))
        except (TypeError, ValueError):
            pass

    explicit_pos = result.get("position") if isinstance(result.get("position"), dict) else None
    if explicit_pos is None and isinstance((payload or {}).get("position"), dict):
        explicit_pos = (payload or {}).get("position")

    position_hint_raw = str((payload or {}).get("position_hint") or "").strip().lower()
    semantic_hints = {"next_line", "left", "center", "right", "auto"}
    has_semantic_hint = position_hint_raw in semantic_hints

    # Honor explicit numeric positions only when there is no semantic layout hint.
    # Semantic hints (next_line/left/center/right) are resolved by the layout engine.
    if (
        not has_semantic_hint
        and isinstance(explicit_pos, dict)
        and "x" in explicit_pos
        and "y" in explicit_pos
    ):
        try:
            rect = Rect(float(explicit_pos["x"]), float(explicit_pos["y"]), w, h).clamp_to_board()
            if state.collides(rect):
                region = choose_region(region_hint, state, document=document)
                rect = _next_position(region, state, w, h)
                result["region"] = region
            result["position"] = {"x": rect.x, "y": rect.y}
            result["size"] = {"w": rect.w, "h": rect.h}
            if isinstance(result.get("payload"), dict):
                result["payload"] = {**result["payload"], "position": result["position"], "size": result["size"]}
            state.add(rect)
            return result
        except (TypeError, ValueError):
            pass

    relative_to = result.get("relative_to") or (payload or {}).get("related_concept")
    region = choose_region(region_hint, state, relative_to=relative_to, document=document)
    rect = _next_position(region, state, w, h)

    # Apply horizontal semantic hint within the resolved region.
    area = region_rect(region)
    if position_hint_raw == "center":
        cx = area.x + max(0.0, (area.w - rect.w) / 2)
        candidate = Rect(cx, rect.y, rect.w, rect.h).clamp_to_board()
        if not state.collides(candidate):
            rect = candidate
    elif position_hint_raw == "right":
        rx = area.x2 - rect.w
        candidate = Rect(rx, rect.y, rect.w, rect.h).clamp_to_board()
        if not state.collides(candidate):
            rect = candidate
    elif position_hint_raw == "left":
        lx = area.x
        candidate = Rect(lx, rect.y, rect.w, rect.h).clamp_to_board()
        if not state.collides(candidate):
            rect = candidate

    result["region"] = region
    result["position"] = {"x": rect.x, "y": rect.y}
    result["size"] = {"w": rect.w, "h": rect.h}
    if isinstance(result.get("payload"), dict):
        result["payload"] = {**result["payload"], "position": result["position"], "size": result["size"]}
    state.add(rect)
    return result


def layout_plan(
    plan: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
    *,
    default_region: str | None = None,
) -> dict[str, Any]:
    """Assign positions to all placeable actions in a structured plan."""
    state = build_layout_state(document)
    actions = list(plan.get("actions") or [])
    placed: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        placed.append(place_action(action, state, document=document, default_region=default_region))
    out = dict(plan)
    out["actions"] = placed
    return out


class BlackboardLayoutManager:
    """Deterministic layout engine for blackboard elements.

    This is the single backend authority for placing text, equations, and
    generated visuals on the normalized board canvas. It uses occupied regions
    from the current document and collision checks to avoid overlap.
    """

    def __init__(self, document: Mapping[str, Any] | None = None):
        self.document = document or {"elements": []}
        self.state = build_layout_state(self.document)

    def refresh(self, document: Mapping[str, Any] | None) -> None:
        self.document = document or {"elements": []}
        self.state = build_layout_state(self.document)

    def place_payload(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str = "text",
        default_region: str | None = None,
    ) -> dict[str, Any]:
        """Return payload copy with validated normalized position/size."""
        action = {"type": kind, "payload": dict(payload), **{k: v for k, v in payload.items() if k in {"position", "size", "region", "position_hint", "side_preference", "related_concept"}}}
        placed = place_action(action, self.state, document=self.document, default_region=default_region)
        out = dict(payload)
        pos = placed.get("position")
        size = placed.get("size")
        if isinstance(pos, dict):
            out["position"] = {"x": _r(float(pos["x"])), "y": _r(float(pos["y"]))}
        if isinstance(size, dict):
            out["size"] = {"w": _r(float(size["w"])), "h": _r(float(size["h"]))}
        if placed.get("region"):
            out["region"] = placed["region"]
        return out

    def place_text(self, payload: Mapping[str, Any], *, default_region: str | None = None) -> dict[str, Any]:
        style = str(payload.get("style") or "body").lower()
        kind = "write_equation" if style == "equation" else "write_text"
        return self.place_payload(payload, kind=kind, default_region=default_region)

    def find_region_for_visual(
        self,
        *,
        width: float,
        height: float,
        preferred: str | None = None,
    ) -> tuple[str, Rect]:
        """Find a region/rect that can fit a visual of approx width x height."""
        candidates: list[str] = []
        if preferred and preferred in REGION_RECTS:
            candidates.append(preferred)
        candidates.extend([r for r in FALLBACK_ORDER if r not in candidates])
        # Try preferred/full regions first, then any region.
        for name in candidates + ["full"]:
            area = region_rect(name)
            # Fit by scaling into region if needed, but report target rect.
            w = max(0.1, min(width, area.w))
            h = max(0.1, min(height, area.h))
            rect = _next_position(name, self.state, w, h)
            # _next_position scans full board on failure; accept if inside board.
            if rect.w > 0 and rect.h > 0:
                return name, rect
        area = region_rect("full")
        return "full", Rect(area.x, area.y, min(width, area.w), min(height, area.h)).clamp_to_board()


__all__ = [
    "BlackboardLayoutManager",
    "LayoutState",
    "Rect",
    "build_layout_state",
    "choose_region",
    "estimate_equation_size",
    "estimate_text_size",
    "layout_plan",
    "place_action",
    "region_occupancy",
    "region_rect",
]
