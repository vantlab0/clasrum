"""BoardIntentHandler: visual intent -> generative drawing plan -> board event.

Pipeline (Phase 4 — generative AI drawing engine):

  board_intent tool call
    -> validate params
    -> collect compact board summary + recent transcript + lesson state
    -> ask Qwen3.7-Flash for a complete generative drawing plan
       (structured vector drawing instructions, no templates)
    -> validate the plan against the drawing protocol (strict schema,
       bounded geometry, stage/layer references)
    -> on validation failure: ONE bounded correction retry, then fail safely
    -> fit the plan into the requested board region (uniform scale+translate)
    -> append a single ``draw_plan`` board event via BoardService
    -> register transcript triggers and focus targets
    -> return a valid tool result

The realtime model keeps sending lightweight visual intents; the detailed
drawing generation happens asynchronously here so the tutor never waits for
the finished illustration before continuing to speak.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.board import layout
from app.board.drawing_protocol import (
    DrawingPlanError,
    MAX_ELEMENTS,
    MAX_PATH_SEGMENTS,
    MAX_POINTS,
    MAX_STAGES,
    MAX_TEXT_LENGTH,
    parse_drawing_plan,
    plan_element_bounds,
    plan_total_duration_ms,
)
from app.board.registry import ToolRegistry
from app.board.service import BoardService, active_elements
from app.config import get_settings
from app.db.models import Lesson, LessonBoardEvent, LessonCheckpoint, LessonPlan, LessonTranscriptEntry
from app.realtime.transcript_triggers import (
    DEFAULT_COOLDOWN_MS,
    SUPPORTED_TRIGGER_ACTIONS,
    TranscriptTriggerSystem,
)

logger = logging.getLogger(__name__)

# Hard caps to keep the text-model context compact (latency + token control).
MAX_BOARD_SUMMARY_ELEMENTS = 40
MAX_TRANSCRIPT_TURNS = 12
MAX_TRANSCRIPT_CHARS = 3000
MODEL_TIMEOUT_SECONDS = 90.0
#: Validation-failure retries per intent (bounded: never loops on bad output).
MAX_VALIDATION_RETRIES = 1

FOCUS_VALUES = {"none", "new_content", "region"}
REGION_HINTS = {"left", "right", "top", "bottom", "center", "bottom_left", "bottom_right"}


def _semantic_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _intent_key(value: Any) -> str:
    return _semantic_key(re.sub(r"[^\w\s]", " ", str(value or "")))


def duplicate_plan_result(document: Mapping[str, Any] | None, intent: Any) -> dict[str, Any] | None:
    """Return an acknowledgement for an already-present logical visual."""
    intent_key = _intent_key(intent)
    if not intent_key or not document:
        return None
    for element in document.get("elements", []) or []:
        if not isinstance(element, Mapping) or element.get("removed_at_sequence") is not None:
            continue
        if element.get("type") != "draw_plan":
            continue
        payload = element.get("payload") if isinstance(element.get("payload"), Mapping) else {}
        context = payload.get("context") if isinstance(payload.get("context"), Mapping) else {}
        if _intent_key(context.get("intent")) != intent_key:
            continue
        return {
            "ok": True,
            "duplicate": True,
            "message": "visual intent already on the board; skipped duplicate draw",
            "sequence": element.get("created_sequence"),
            "element_id": element.get("element_id"),
        }
    return None

_KNINDS_LINE = "line | polyline | polygon | rectangle | ellipse | arc | quad_bezier | cubic_bezier | path | text | arrow"

_SYSTEM_PROMPT = (
    "You are the Generative Drawing Engine of a live tutoring blackboard. "
    "You invent original educational illustrations from scratch for whatever "
    "concept the tutor is explaining. There are no templates: decide yourself "
    "which structures the concept needs, how they connect, their proportions, "
    "positions, strokes, shading and labels, and express everything as "
    "structured vector drawing instructions.\n"
    "Output STRICT JSON only (no markdown fences, no commentary) with keys: "
    "protocol, version, title, elements, stages.\n"
    f"- elements: 8..18 objects (keep the plan compact). Each has: id (short unique slug), "
    f"kind ({_KNINDS_LINE}), geometry (object per kind), optional style "
    "{stroke_color, stroke_width (0.2..4), stroke_opacity, fill_color, "
    "fill_opacity, line_cap, line_join, dash[]}, layer (0..5, back to front), "
    "stage (1..12), duration_ms (60..6000, how long the stroke takes to draw), "
    "delay_ms (0..4000, pause before this stroke).\n"
    "Geometry per kind: line/arrow {start:{x,y}, end:{x,y}[, curve:-1..1]}; "
    "polyline/polygon {points:[{x,y}...], closed:bool, smooth:bool}; rectangle "
    "{position:{x,y}, size:{w,h}, corner_radius?}; ellipse {center:{x,y}, rx, ry}; "
    "arc {center:{x,y}, rx, ry, start_angle, sweep_angle}; quad_bezier {start, "
    "control, end}; cubic_bezier {start, c1, c2, end}; path {segments:[{op:move|"
    "line|quad|cubic|arc|close, point/control/c1/c2/center/rx/ry/start_angle/"
    "sweep_angle...}]}; text {text, position:{x,y}, align, font_scale?}.\n"
    f"- stages: 3..6 objects {{stage, description, element_ids, "
    "pause_ms}} describing the progressive reveal sequence (outline first, "
    "then internal structures, then details/shading, then labels).\n"
    "Use ONLY the element kinds listed above. NEVER use kind 'circle'; draw a "
    "circle as an ellipse with rx == ry. Every style.stroke_width must be a "
    "number between 0.2 and 4.0; never use 0 or values below 0.2.\n"
    "All coordinates are normalized to the available board region given in the "
    "context (region rect). Use organic paths, quadratic/cubic curves, arcs and "
    "irregular outlines for biological/organic structures; use hatching as "
    "several short parallel path strokes for shading; keep fills subtle "
    "(opacity <= 0.35); chalk palette colors: primary/secondary/blue/yellow/red/green "
    "or #RRGGBB. Every kind=text label must be ONE short line (1-8 words, no "
    "newlines, no full sentences); never put a paragraph in a text element.\n"
    "Rules: draw something original that matches the requested concept; do not "
    "copy generic placeholder diagrams; every stage must build on the previous "
    "one; elements must be listed in drawing order; no executable code, no SVG, "
    "no HTML."
)

_PLAN_SCHEMA_HINT = {
    "protocol": "clasrum.drawing",
    "version": 1,
    "title": "short title of the illustration",
    "elements": [
        {
            "id": "unique_element_id",
            "kind": _KNINDS_LINE,
            "geometry": {"...": "per kind, see system prompt"},
            "style": {
                "stroke_color": "primary|#RRGGBB",
                "stroke_width": 2.0,
                "stroke_opacity": 0.9,
                "fill_color": "#RRGGBB",
                "fill_opacity": 0.2,
                "line_cap": "round",
                "line_join": "round",
                "dash": [],
            },
            "layer": 0,
            "stage": 1,
            "duration_ms": 700,
            "delay_ms": 0,
        }
    ],
    "stages": [
        {
            "stage": 1,
            "description": "what this stage draws",
            "element_ids": ["unique_element_id"],
            "pause_ms": 150,
        }
    ],
}


class IntentHandlingError(Exception):
    """Controlled failure that becomes a tool error (never crashes the session)."""

    def __init__(self, message: str, *, code: str = "intent_failed", retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable

    def to_tool_result(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, "retryable": self.retryable}


@dataclass
class IntentContext:
    board_summary: list[dict[str, Any]]
    lesson_state: dict[str, Any]
    transcript: list[dict[str, str]]
    intent: str
    focus: str
    region_hint: str | None


def _clean_text(value: Any, limit: int = 400) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _element_label(element: Mapping[str, Any]) -> str:
    payload = element.get("payload") or {}
    for key in ("text", "label", "term", "title", "latex", "expression"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return _clean_text(val, 120)
    items = payload.get("items")
    if isinstance(items, list) and items:
        return _clean_text("; ".join(str(i) for i in items[:4]), 120)
    return ""


def summarize_board(document: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Compact summary of live elements for the text model."""
    if not document:
        return []
    summary: list[dict[str, Any]] = []
    for element in active_elements(dict(document))[:MAX_BOARD_SUMMARY_ELEMENTS]:
        pos = element.get("position") or {}
        size = element.get("size") or {}
        item: dict[str, Any] = {
            "id": element.get("element_id"),
            "type": element.get("type"),
            "label": _element_label(element),
        }
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            item["pos"] = {"x": round(float(pos["x"]), 3), "y": round(float(pos["y"]), 3)}
        if isinstance(size, dict) and "w" in size and "h" in size:
            item["size"] = {"w": round(float(size["w"]), 3), "h": round(float(size["h"]), 3)}
        payload = element.get("payload") or {}
        # Lightweight relationship hints.
        rels: dict[str, Any] = {}
        if payload.get("coordinate_system_id"):
            rels["coordinate_system_id"] = payload["coordinate_system_id"]
        if payload.get("target_ids"):
            rels["target_ids"] = payload["target_ids"]
        if rels:
            item["relations"] = rels
        summary.append(item)
    return summary


async def fetch_recent_transcript(db: AsyncSession, lesson_id: str) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            select(LessonTranscriptEntry)
            .where(LessonTranscriptEntry.lesson_id == lesson_id)
            .order_by(LessonTranscriptEntry.sequence.desc())
            .limit(MAX_TRANSCRIPT_TURNS)
        )
    ).scalars().all()
    turns: list[dict[str, str]] = []
    total = 0
    for row in reversed(rows):
        text = _clean_text(row.text, 600)
        if not text:
            continue
        if total + len(text) > MAX_TRANSCRIPT_CHARS:
            break
        turns.append({"speaker": row.speaker, "text": text})
        total += len(text)
    return turns


async def fetch_lesson_context(db: AsyncSession, lesson_id: str) -> dict[str, Any]:
    """Topic/concept/progress from lesson row, plan and checkpoint resume index."""
    ctx: dict[str, Any] = {}
    lesson = await db.get(Lesson, lesson_id)
    if lesson is not None:
        ctx["topic"] = lesson.topic
        ctx["subject"] = lesson.subject
        ctx["current_concept"] = lesson.current_concept
        ctx["progress"] = lesson.progress
        ctx["status"] = lesson.status
        meta = lesson.session_metadata or {}
        if isinstance(meta, dict) and meta.get("current_concept_title"):
            ctx["current_concept_title"] = meta.get("current_concept_title")
    plan_row = (
        await db.execute(select(LessonPlan).where(LessonPlan.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if plan_row is not None:
        concepts = [c for c in (plan_row.concepts or []) if isinstance(c, dict)]
        ctx["concepts"] = [
            {"id": c.get("id"), "title": c.get("title"), "status": c.get("status")}
            for c in concepts[:20]
        ]
        ctx["covered_concepts"] = [c.get("id") for c in concepts if c.get("status") == "completed"]
        ctx["pending_concepts"] = [c.get("id") for c in concepts if c.get("status") != "completed"]
    ckpt = (
        await db.execute(select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if ckpt is not None and isinstance(ckpt.state, dict):
        st = ckpt.state
        for key in ("current_concept", "progress", "completed_concepts", "pending_concepts"):
            if st.get(key) is not None:
                ctx[key] = st.get(key)
        ts = st.get("teaching_state") if isinstance(st.get("teaching_state"), dict) else {}
        if ts.get("recommended_action"):
            ctx["recommended_action"] = ts.get("recommended_action")
    return ctx


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model response")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response JSON is not an object")
    return payload


async def call_board_director(context: IntentContext) -> dict[str, Any]:
    """Call Qwen3.7-Flash (OpenAI-compatible) and return the raw plan dict.

    Raises :class:`IntentHandlingError` for timeouts, rate limits, bad response
    shapes and unparseable JSON — the caller decides whether to retry once.
    """
    settings = get_settings()
    api_key = settings.effective_board_director_api_key
    if not api_key:
        raise IntentHandlingError(
            "Board director model is not configured", code="qwen_unavailable", retryable=False
        )
    region_rect = layout.REGION_RECTS.get(str(context.region_hint or "full"), layout.REGION_RECTS["full"])
    user_payload = {
        "lesson_context": {
            "topic": context.lesson_state.get("topic"),
            "subject": context.lesson_state.get("subject"),
            "current_concept": context.lesson_state.get("current_concept")
            or context.lesson_state.get("current_concept_title"),
            "concept_list": context.lesson_state.get("concepts"),
        },
        "recent_transcript": context.transcript,
        "current_board_content": context.board_summary,
        "visual_intent": context.intent,
        "focus": context.focus,
        "available_region": {
            "name": context.region_hint or "full",
            "x": region_rect[0],
            "y": region_rect[1],
            "w": region_rect[2],
            "h": region_rect[3],
        },
        "canvas": {"width": 1.0, "height": 1.0, "origin": "top_left", "y_down": True},
        "drawing_protocol": {
            "name": "clasrum.drawing",
            "version": 1,
            "limits": {
                "max_elements": MAX_ELEMENTS,
                "max_stages": MAX_STAGES,
                "max_path_segments": MAX_PATH_SEGMENTS,
                "max_points_per_polyline": MAX_POINTS,
                "max_text_length": MAX_TEXT_LENGTH,
            },
            "response_schema_hint": _PLAN_SCHEMA_HINT,
        },
    }
    body = {
        "model": settings.board_director_model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        # Qwen3 flash is much faster in non-thinking mode for structured drawing plans.
        "enable_thinking": False,
    }
    timeout = min(float(settings.board_director_timeout_seconds), MODEL_TIMEOUT_SECONDS)
    url = settings.board_director_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    max_attempts = 3
    last_exc: IntentHandlingError | None = None

    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        logger.info(
            "board_director request_started model=%s timeout_s=%.1f intent_chars=%s attempt=%s/%s",
            settings.board_director_model,
            timeout,
            len(context.intent),
            attempt,
            max_attempts,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=body)
            duration_ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "board_director response_received duration_ms=%.1f status=%s model=%s attempt=%s",
                duration_ms,
                response.status_code,
                settings.board_director_model,
                attempt,
            )
            if response.status_code == 429:
                raise IntentHandlingError(
                    "Board director is rate limited", code="qwen_rate_limited", retryable=True
                )
            if response.status_code >= 500:
                raise IntentHandlingError(
                    f"Board director returned HTTP {response.status_code}",
                    code="qwen_unavailable",
                    retryable=True,
                )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except IntentHandlingError as exc:
            last_exc = exc
            if exc.retryable and attempt < max_attempts:
                backoff = 0.4 * attempt
                logger.warning(
                    "board_director retrying attempt=%s/%s code=%s backoff_s=%.1f",
                    attempt,
                    max_attempts,
                    exc.code,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            last_exc = IntentHandlingError("Board director timed out", code="qwen_unavailable", retryable=True)
            logger.warning("board_director timeout attempt=%s: %s", attempt, exc)
            if attempt < max_attempts:
                await asyncio.sleep(0.4 * attempt)
                continue
            raise last_exc from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status >= 500 or status == 429
            last_exc = IntentHandlingError(
                f"Board director returned HTTP {status}",
                code="qwen_unavailable",
                retryable=retryable,
            )
            logger.warning("board_director http_error status=%s attempt=%s", status, attempt)
            if retryable and attempt < max_attempts:
                await asyncio.sleep(0.4 * attempt)
                continue
            raise last_exc from exc
        except httpx.HTTPError as exc:
            last_exc = IntentHandlingError("Board director request failed", code="qwen_unavailable", retryable=True)
            logger.warning("board_director request_failed attempt=%s: %s", attempt, exc)
            if attempt < max_attempts:
                await asyncio.sleep(0.4 * attempt)
                continue
            raise last_exc from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("board_director bad_response_shape attempt=%s: %s", attempt, exc)
            raise IntentHandlingError("Board director response shape was invalid", retryable=True) from exc

        try:
            plan = _extract_json(content if isinstance(content, str) else json.dumps(content))
        except Exception as exc:
            logger.warning("board_director json_parse_failed attempt=%s: %s", attempt, exc)
            raise IntentHandlingError("Board director returned invalid JSON", retryable=True) from exc
        return plan

    if last_exc is not None:
        raise last_exc
    raise IntentHandlingError("Board director failed", code="qwen_unavailable", retryable=True)


_COLOR_ROLES = {"primary", "secondary", "blue", "yellow", "red", "green"}
_COLOR_ALIASES = {
    "white": "primary",
    "black": "primary",
    "grey": "secondary",
    "gray": "secondary",
    "orange": "yellow",
    "purple": "blue",
    "brown": "red",
    "pink": "red",
}
_CLOSED_KINDS = {"polygon", "rectangle", "ellipse"}


def _repair_color(value: Any, default: str = "primary") -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    lowered = text.lower()
    if lowered in _COLOR_ROLES:
        return lowered
    if (
        len(text) == 7
        and text.startswith("#")
        and all(ch in "0123456789abcdefABCDEF" for ch in text[1:])
    ):
        return "#" + text[1:].upper()
    return _COLOR_ALIASES.get(lowered, default)


def _clamp(value: Any, low: float, high: float, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def _repair_path_segments(raw_segments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, list):
        return []
    repaired: list[dict[str, Any]] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        seg = dict(raw)
        op = str(seg.get("op") or "").lower()
        if op not in {"move", "line", "quad", "cubic", "arc", "close"}:
            continue
        if op in {"move", "line"}:
            if not isinstance(seg.get("point"), dict):
                continue
        elif op == "quad":
            if not isinstance(seg.get("control"), dict) or not isinstance(seg.get("point"), dict):
                if isinstance(seg.get("point"), dict):
                    seg["op"] = "line"
                else:
                    continue
        elif op == "cubic":
            if (
                not isinstance(seg.get("c1"), dict)
                or not isinstance(seg.get("c2"), dict)
                or not isinstance(seg.get("point"), dict)
            ):
                if isinstance(seg.get("point"), dict):
                    seg["op"] = "line"
                else:
                    continue
        elif op == "arc":
            required = ("center", "rx", "ry", "start_angle", "sweep_angle")
            if any(seg.get(key) is None for key in required):
                continue
        repaired.append(seg)
    if repaired and str(repaired[0].get("op")) == "close":
        return []
    return repaired


def _repair_element(raw: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    element = dict(raw)
    kind = str(element.get("kind") or "").strip().lower()
    if kind in {"circle", "oval"}:
        kind = "ellipse"
    if kind not in {
        "line", "polyline", "polygon", "rectangle", "ellipse", "arc",
        "quad_bezier", "cubic_bezier", "path", "text", "arrow",
    }:
        # Leave unsupported kinds untouched so strict validation can reject them
        # and trigger the normal bounded correction retry.
        return element
    geometry = element.get("geometry")
    if not isinstance(geometry, dict):
        return element
    geometry = dict(geometry)
    if kind == "ellipse":
        radius = geometry.get("radius", geometry.get("r"))
        if radius is not None:
            geometry.setdefault("rx", radius)
            geometry.setdefault("ry", radius)
        if "center" not in geometry and isinstance(geometry.get("position"), dict):
            geometry["center"] = geometry["position"]
    if kind == "path":
        segments = _repair_path_segments(geometry.get("segments"))
        if not segments:
            return None
        geometry["segments"] = segments
    if kind == "text":
        text = geometry.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        geometry["text"] = text[:500]
        if "font_scale" in geometry:
            font_scale = _clamp(geometry.get("font_scale"), 0.5, 2.5)
            if font_scale is None:
                geometry.pop("font_scale", None)
            else:
                geometry["font_scale"] = font_scale
        if "align" in geometry and geometry.get("align") not in {"left", "center", "right"}:
            geometry.pop("align", None)

    segment_list = geometry.get("segments") if kind == "path" else None
    closed = (
        kind in _CLOSED_KINDS
        or (kind == "polyline" and bool(geometry.get("closed")))
        or (
            kind == "path"
            and isinstance(segment_list, list)
            and any(str(seg.get("op")) == "close" for seg in segment_list if isinstance(seg, dict))
        )
    )
    style = element.get("style")
    style = dict(style) if isinstance(style, dict) else {}
    if "stroke_width" in style:
        width = _clamp(style.get("stroke_width"), 0.2, 4.0)
        style["stroke_width"] = width if width is not None else 1.0
    if "stroke_color" in style:
        style["stroke_color"] = _repair_color(style.get("stroke_color"))
    if "fill_color" in style:
        style["fill_color"] = _repair_color(style.get("fill_color"))
    for key in ("stroke_opacity", "fill_opacity"):
        if key in style:
            opacity = _clamp(style.get(key), 0.0, 1.0)
            if opacity is None:
                style.pop(key, None)
            else:
                style[key] = opacity
    if "dash" in style:
        raw_dash = style.get("dash")
        if isinstance(raw_dash, list):
            dash = [
                value
                for value in (
                    _clamp(item, 0.05, 50.0) for item in raw_dash[:16]
                )
                if value is not None
            ]
            if dash:
                style["dash"] = dash
            else:
                style.pop("dash", None)
        else:
            style.pop("dash", None)
    for key, allowed in (
        ("line_cap", {"butt", "round", "square"}),
        ("line_join", {"miter", "round", "bevel"}),
    ):
        if key in style and style.get(key) not in allowed:
            style.pop(key, None)
    if not closed:
        style.pop("fill_color", None)
        style.pop("fill_opacity", None)

    raw_id = str(element.get("id") or f"element_{index + 1}").strip()
    clean_id = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in raw_id)[:64]
    if not clean_id or not clean_id[0].isalnum():
        clean_id = f"element_{index + 1}"

    stage = int(_clamp(element.get("stage"), 1, 12, 1) or 1)
    layer = int(_clamp(element.get("layer"), 0, 5, 1) or 0)
    duration = int(_clamp(element.get("duration_ms"), 60, 6000, 500) or 500)
    delay = int(_clamp(element.get("delay_ms"), 0, 4000, 0) or 0)

    return {
        "id": clean_id,
        "kind": kind,
        "geometry": geometry,
        "style": style,
        "layer": layer,
        "stage": stage,
        "duration_ms": duration,
        "delay_ms": delay,
    }


def _repair_drawing_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Repair common model deviations before strict protocol validation."""
    source = dict(plan)
    raw_elements = source.get("elements") if isinstance(source.get("elements"), list) else []
    elements: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    used_labels: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_elements):
        element = _repair_element(raw, index)
        if element is None:
            continue
        if element["kind"] == "text":
            geometry = element.get("geometry") if isinstance(element.get("geometry"), Mapping) else {}
            position = geometry.get("position") if isinstance(geometry.get("position"), Mapping) else {}
            pos_key = ":".join(
                f"{round(float(position.get(axis) or 0.0), 3)}" for axis in ("x", "y")
            )
            label_key = (_semantic_key(geometry.get("text")), pos_key)
            if label_key in used_labels:
                continue
            used_labels.add(label_key)
        element_id = element["id"]
        if element_id in used_ids:
            suffix = 2
            while f"{element_id}_{suffix}" in used_ids:
                suffix += 1
            element_id = f"{element_id}_{suffix}"[:64]
            element["id"] = element_id
        used_ids.add(element_id)
        elements.append(element)

    if not elements:
        return source

    raw_stages = source.get("stages") if isinstance(source.get("stages"), list) else []
    original_stages: dict[int, dict[str, Any]] = {}
    for raw_stage in raw_stages:
        if not isinstance(raw_stage, dict):
            continue
        stage_num = int(_clamp(raw_stage.get("stage"), 1, 12, 1) or 1)
        original_stages[stage_num] = raw_stage

    by_stage: dict[int, list[str]] = {}
    for element in elements:
        stage_num = int(_clamp(element.get("stage"), 1, 12, 1) or 1)
        element["stage"] = stage_num
        stage_ids = by_stage.setdefault(stage_num, [])
        element_id = str(element.get("id", ""))
        if element_id not in stage_ids:
            stage_ids.append(element_id)

    stages: list[dict[str, Any]] = []
    for stage_num in sorted(by_stage):
        original = original_stages.get(stage_num, {})
        description = str(original.get("description") or "")[:240]
        pause = int(_clamp(original.get("pause_ms"), 0, 3000, 0) or 0)
        stages.append(
            {
                "stage": stage_num,
                "description": description,
                "element_ids": by_stage[stage_num],
                "pause_ms": pause,
            }
        )

    title = source.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Lesson illustration"
    repaired: dict[str, Any] = {
        "protocol": "clasrum.drawing",
        "version": 1,
        "title": title[:200],
        "elements": elements,
        "stages": stages,
    }

    focus = source.get("focus")
    if isinstance(focus, dict):
        target_ids = focus.get("target_ids")
        if isinstance(target_ids, list):
            clean_targets: list[str] = []
            for target in target_ids:
                target_id = str(target)
                if target_id in used_ids and target_id not in clean_targets:
                    clean_targets.append(target_id)
        else:
            clean_targets = []
        repaired["focus"] = {**focus, "target_ids": clean_targets}
    triggers = source.get("triggers")
    if isinstance(triggers, list):
        repaired["triggers"] = [
            trigger
            for trigger in triggers
            if isinstance(trigger, dict) and str(trigger.get("target")) in used_ids
        ]
    return repaired


def validate_drawing_plan(plan: Mapping[str, Any], *, repair: bool = False) -> dict[str, Any]:
    """Validate + normalize a generative drawing plan (protocol v1).

    Args:
        plan: the raw plan dict from the model.
        repair: if True, apply model deviation repairs before strict validation.

    Returns the cleaned plan payload ready for the ``draw_plan`` event.
    Raises :class:`IntentHandlingError` with a model-actionable message.
    """
    if repair:
        plan = _repair_drawing_plan(plan)
    try:
        parsed = parse_drawing_plan(plan)
    except DrawingPlanError as exc:
        raise IntentHandlingError(
            f"invalid drawing plan: {exc}", code="invalid_arguments", retryable=True
        ) from None
    return parsed.to_payload()


def fit_plan_to_region(
    payload: dict[str, Any], region: tuple[float, float, float, float]
) -> dict[str, Any]:
    """Uniformly scale + translate a validated plan payload into ``region``.

    Preserves the illustration's aspect ratio and internal proportions. Plans
    that already fit inside the region pass through unchanged.
    """
    boxes = plan_element_bounds(payload)
    if not boxes:
        return payload
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[0] + box[2] for box in boxes)
    max_y = max(box[1] + box[3] for box in boxes)
    rx, ry, rw, rh = region
    if min_x >= rx - 1e-6 and min_y >= ry - 1e-6 and max_x <= rx + rw + 1e-6 and max_y <= ry + rh + 1e-6:
        return payload  # already inside the requested region
    width = max(max_x - min_x, 1e-6)
    height = max(max_y - min_y, 1e-6)
    scale = min(rw / width, rh / height) * 0.94
    scale = max(0.02, min(scale, 4.0))
    center_x = rx + rw / 2 - scale * (min_x + max_x) / 2
    center_y = ry + rh / 2 - scale * (min_y + max_y) / 2

    def point(p: Any) -> dict[str, float]:
        if not isinstance(p, dict):
            return {"x": 0.0, "y": 0.0}
        x = p.get("x")
        y = p.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return {"x": 0.0, "y": 0.0}
        return {"x": round(x * scale + center_x, 4), "y": round(y * scale + center_y, 4)}

    def clamp01(value: float) -> float:
        return round(min(1.0, max(0.0, value)), 4)

    for element in payload.get("elements", []):
        if not isinstance(element, dict):
            continue
        kind = element.get("kind")
        geometry = element.get("geometry")
        if not isinstance(geometry, dict):
            continue
        element["geometry"] = geometry = dict(geometry)
        if kind in {"line", "arrow"}:
            geometry["start"] = point(geometry.get("start"))
            geometry["end"] = point(geometry.get("end"))
        elif kind in {"polyline", "polygon"}:
            geometry["points"] = [point(p) for p in (geometry.get("points") or [])]
        elif kind == "rectangle":
            position = geometry.get("position") or {}
            size = geometry.get("size") or {}
            new_pos = point(position)
            new_w = _num(size.get("w"), 0.0) * scale
            new_h = _num(size.get("h"), 0.0) * scale
            geometry["position"] = new_pos
            geometry["size"] = {
                "w": clamp01(new_w) or 0.001,
                "h": clamp01(new_h) or 0.001,
            }
        elif kind in {"ellipse", "arc"}:
            geometry["center"] = point(geometry.get("center"))
            rx_value = _num(geometry.get("rx"), 0.0) * scale
            ry_value = _num(geometry.get("ry"), 0.0) * scale
            geometry["rx"] = clamp01(rx_value) or 0.001
            geometry["ry"] = clamp01(ry_value) or 0.001
            if kind == "ellipse":
                geometry["rx"] = max(geometry["rx"], 0.001)
                geometry["ry"] = max(geometry["ry"], 0.001)
        elif kind in {"quad_bezier", "cubic_bezier"}:
            geometry["start"] = point(geometry.get("start"))
            geometry["end"] = point(geometry.get("end"))
            for key in ("control", "c1", "c2"):
                if geometry.get(key) is not None:
                    geometry[key] = point(geometry[key])
        elif kind == "path":
            segments = []
            for segment in geometry.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                new_segment = dict(segment)
                for key in ("point", "control", "c1", "c2", "center"):
                    if new_segment.get(key) is not None:
                        new_segment[key] = point(new_segment[key])
                for key in ("rx", "ry"):
                    if new_segment.get(key) is not None:
                        new_segment[key] = clamp01(_num(new_segment[key], 0.0) * scale) or 0.001
                segments.append(new_segment)
            geometry["segments"] = segments
        elif kind == "text":
            geometry["position"] = point(geometry.get("position"))
            font_scale = _num(geometry.get("font_scale"), 1.0) * scale
            # Protocol v1 and the Flutter parser both require [0.5, 2.5].
            geometry["font_scale"] = round(max(0.5, min(2.5, font_scale)), 4)
    return payload


def _num(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


class BoardIntentHandler:
    """Orchestrates intent -> generative plan -> board event."""

    def __init__(
        self,
        board: BoardService | None = None,
        registry: ToolRegistry | None = None,
        trigger_system: TranscriptTriggerSystem | None = None,
    ) -> None:
        self._board = board or BoardService()
        self._registry = registry or ToolRegistry(board=self._board)
        self._trigger_system = trigger_system

    async def handle(
        self,
        db: AsyncSession,
        lesson_id: str,
        arguments: Mapping[str, Any],
        *,
        source: str = "qwen_tool",
        animate: bool = True,
    ) -> dict[str, Any]:
        intent = _clean_text(arguments.get("intent"), 1000)
        if not intent:
            return {"ok": False, "error": "invalid_arguments", "message": "intent is required", "retryable": True}
        focus = str(arguments.get("focus") or "new_content")
        if focus not in FOCUS_VALUES:
            focus = "new_content"
        region_hint = arguments.get("region_hint")
        if region_hint is not None and str(region_hint) not in REGION_HINTS:
            region_hint = None

        logger.info("board_intent received lesson=%s focus=%s intent=%s", lesson_id, focus, intent[:160])
        try:
            document = await self._board.reconstruct_document(db, lesson_id)
        except Exception:
            # Missing/empty board state is valid (new lesson) – start from empty.
            document = {"elements": [], "last_sequence": 0}
        board_summary = summarize_board(document)
        duplicate = duplicate_plan_result(document, intent)
        if duplicate is not None:
            return duplicate
        lesson_state = await fetch_lesson_context(db, lesson_id)
        transcript = await fetch_recent_transcript(db, lesson_id)

        t_model = time.perf_counter()
        plan_payload, model_error = await self._generate_plan(
            IntentContext(
                board_summary=board_summary,
                lesson_state=lesson_state,
                transcript=transcript,
                intent=intent,
                focus=focus,
                region_hint=str(region_hint) if region_hint else None,
            ),
        )
        logger.info(
            "board_director total_ms=%.1f lesson=%s", (time.perf_counter() - t_model) * 1000, lesson_id
        )
        if model_error is not None or plan_payload is None:
            assert model_error is not None
            raise model_error

        # Place the visual in an available region, not over existing content.
        boxes = plan_element_bounds(plan_payload)
        if boxes:
            min_x = min(b[0] for b in boxes)
            min_y = min(b[1] for b in boxes)
            max_x = max(b[0] + b[2] for b in boxes)
            max_y = max(b[1] + b[3] for b in boxes)
            plan_w = max(0.1, max_x - min_x)
            plan_h = max(0.1, max_y - min_y)
        else:
            plan_w, plan_h = 0.52, 0.36

        # Re-read state under the lesson lock: the director may have taken
        # seconds, during which other tools could have written to the board.
        async with layout.board_layout_lock(lesson_id):
            try:
                document = await self._board.reconstruct_document(db, lesson_id)
            except Exception:
                document = {"elements": [], "last_sequence": 0}
            duplicate = duplicate_plan_result(document, intent)
            if duplicate is not None:
                return duplicate
            manager = layout.BlackboardLayoutManager(document)
            preferred_region = str(region_hint) if region_hint else None
            try:
                _, region_rect = manager.find_region_for_visual(
                    width=plan_w, height=plan_h, preferred=preferred_region
                )
            except layout.LayoutNoSpaceError as exc:
                raise IntentHandlingError(str(exc), code=exc.code, retryable=True) from None
            region = (region_rect.x, region_rect.y, region_rect.w, region_rect.h)
            plan_payload = fit_plan_to_region(plan_payload, region)
            # Fitting mutates geometry and font_scale; validate the exact bytes
            # that will be persisted, not the pre-fit plan.
            plan_payload = validate_drawing_plan(plan_payload, repair=False)

            payload = {
                **plan_payload,
                "context": {
                    "topic": lesson_state.get("topic"),
                    "subject": lesson_state.get("subject"),
                    "current_concept": lesson_state.get("current_concept")
                    or lesson_state.get("current_concept_title"),
                    "region": region_hint or "full",
                    "intent": intent,
                },
            }
            record = await self._board.append_event(
                db,
                lesson_id,
                "draw_plan",
                payload,
                source=source,
                animate=animate,
            )

        id_map = {element["id"]: record.element_id for element in plan_payload.get("elements", [])
                  if isinstance(element, dict) and element.get("id")}
        registered_triggers = await self._register_triggers(db, lesson_id, plan_payload, id_map)
        focus_out = self._resolve_focus(plan_payload, id_map)

        logger.info(
            "board_intent completed lesson=%s elements=%d duration_ms=%d",
            lesson_id,
            len(plan_payload.get("elements", [])),
            plan_total_duration_ms(plan_payload),
        )
        return {
            "ok": True,
            "intent": intent,
            "event_id": record.event_id,
            "sequence": record.sequence,
            "element_id": record.element_id,
            "plan": {
                "title": plan_payload.get("title"),
                "element_count": len(plan_payload.get("elements", [])),
                "stage_count": len(plan_payload.get("stages", [])),
                "duration_ms": plan_total_duration_ms(plan_payload),
            },
            "focus": focus_out,
            "triggers_registered": registered_triggers,
        }

    async def _generate_plan(
        self,
        context: IntentContext,
    ) -> tuple[dict[str, Any] | None, IntentHandlingError | None]:
        """Call the model and validate; one bounded correction retry on failure.

        Returns ``(plan_payload, None)`` on success or ``(None, error)``.
        """
        last_error: IntentHandlingError | None = None
        feedback = ""
        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            attempt_context = context
            if feedback:
                attempt_context = IntentContext(
                    board_summary=context.board_summary,
                    lesson_state=context.lesson_state,
                    transcript=context.transcript,
                    intent=(
                        f"{context.intent}\n\nYour previous attempt was rejected by the "
                        f"drawing protocol validator: {feedback} Produce a corrected plan."
                    ),
                    focus=context.focus,
                    region_hint=context.region_hint,
                )
            try:
                raw_plan = await call_board_director(attempt_context)
            except IntentHandlingError as exc:
                # Transport/model failures are not corrected by re-prompting.
                return None, exc
            try:
                validated = validate_drawing_plan(raw_plan, repair=True)
                logger.info(
                    "drawing plan validation_result ok=true attempt=%d elements=%s stages=%s",
                    attempt + 1,
                    len(validated.get("elements", [])) if isinstance(validated, dict) else None,
                    len(validated.get("stages", [])) if isinstance(validated, dict) else None,
                )
                return validated, None
            except IntentHandlingError as exc:
                last_error = exc
                feedback = str(exc.message)[:400]
                logger.warning(
                    "drawing plan validation_result ok=false attempt=%d code=%s message=%s",
                    attempt + 1,
                    exc.code,
                    feedback,
                )
        return None, last_error

    async def _register_triggers(
        self,
        db: AsyncSession,
        lesson_id: str,
        plan_payload: dict[str, Any],
        id_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Register transcript triggers declared by the plan (best-effort)."""
        trigger_system = self._trigger_system
        if trigger_system is None:
            return []
        registered: list[dict[str, Any]] = []
        raw_triggers = plan_payload.get("triggers")
        if not isinstance(raw_triggers, list):
            return []
        seen: set[tuple[str, str]] = set()
        for raw in raw_triggers:
            if not isinstance(raw, dict):
                continue
            phrase = str(raw.get("phrase") or "").strip()
            target = str(raw.get("target") or "").strip()
            action = str(raw.get("action") or "highlight").strip().lower()
            if not phrase or not target or action not in SUPPORTED_TRIGGER_ACTIONS:
                continue
            key = (phrase.lower(), target)
            if key in seen:
                continue
            seen.add(key)
            real_target = id_map.get(target, target)
            try:
                cooldown_ms = int(raw.get("cooldown_ms"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                cooldown_ms = DEFAULT_COOLDOWN_MS
            max_fires = raw.get("max_fires")
            try:
                tid = trigger_system.register_trigger(
                    lesson_id,
                    phrase,
                    real_target,
                    action,
                    cooldown_ms=cooldown_ms,
                    max_fires=max_fires,
                )
                registered.append(
                    {
                        "trigger_id": tid,
                        "phrase": phrase,
                        "target_id": real_target,
                        "action": action,
                        "cooldown_ms": cooldown_ms,
                    }
                )
            except ValueError as exc:
                logger.warning(
                    "board_intent trigger registration failed lesson=%s: %s", lesson_id, exc
                )
        return registered

    @staticmethod
    def _resolve_focus(plan_payload: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
        focus = plan_payload.get("focus") if isinstance(plan_payload.get("focus"), dict) else {}
        target_ids = focus.get("target_ids")
        remapped = [id_map.get(str(t), str(t)) for t in target_ids] if isinstance(target_ids, list) else []
        return {
            "target_ids": remapped,
            "priority": str(focus.get("priority") or "normal"),
        }


__all__ = [
    "BoardIntentHandler",
    "IntentContext",
    "IntentHandlingError",
    "call_board_director",
    "fetch_lesson_context",
    "fetch_recent_transcript",
    "fit_plan_to_region",
    "summarize_board",
    "validate_drawing_plan",
]
