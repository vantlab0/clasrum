"""BoardIntentHandler: visual intent -> structured board plan -> board events.

Pipeline (docs in task spec):
  board_intent tool call
    -> validate params
    -> collect compact board summary + recent transcript + lesson state
    -> ask Qwen text model for a structured JSON plan
    -> validate plan actions against supported board capabilities
    -> layout (region-based, deterministic)
    -> convert to existing low-level board events
    -> execute via BoardService
    -> return a valid tool result

All model output is treated as untrusted: it is parsed, schema-validated and
mapped to the existing registry/BoardService validators before any mutation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.board import layout
from app.board.registry import ToolRegistry
from app.board.service import BoardService, BoardValidationError, active_elements
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
MAX_PLAN_ACTIONS = 12
MODEL_TIMEOUT_SECONDS = 20.0

# Board-director action types the model may request. Anything else is rejected.
# These map to existing low-level board tools/events (no unsupported inventions).
SUPPORTED_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "add_node",  # -> write_text/definition styled block
        "add_text",
        "write_text",
        "write_heading",
        "write_definition",
        "write_bullet_list",
        "write_equation",
        "draw_diagram",
        "create_coordinate_system",
        "plot_function",
        "plot_points",
        "add_graph_label",
        "draw_arrow",
        "draw_line",
        "draw_shape",
        "annotate",  # -> highlight existing targets (no new placement)
        "highlight",  # explicit highlight existing targets
    }
)

FOCUS_VALUES = {"none", "new_content", "region"}
REGION_HINTS = {"left", "right", "top", "bottom", "center", "bottom_left", "bottom_right"}
HIGHLIGHT_STYLES = {"box", "underline", "pulse", "circle"}

#: Template IDs the backend validates against. The full geometry lives in the
#: Flutter template registry; the backend only needs to know which IDs are valid.
VALID_TEMPLATE_IDS: frozenset[str] = frozenset(
    {
        "mitochondria",
        "animal_cell",
        "electric_circuit",
        "triangle",
        "coordinate_axes",
        "atom",
    }
)

_SYSTEM_PROMPT = (
    "You are a Board Director for a live tutoring blackboard. "
    "Given the current board, lesson state, recent transcript and a visual intent, "
    "return ONLY a JSON object describing what to draw. "
    "Do not include markdown fences or commentary.\n"
    "Rules: (1) Request only supported actions; never invent element types. "
    "(2) Do not calculate exact pixel/normalized coordinates – provide a region or "
    "relative_to hint and the layout engine will position elements. "
    "(3) Keep labels concise and readable. (4) Prefer updating/annotating existing "
    "elements (by id) over duplicating content. (5) Return valid JSON matching the schema."
)

_PLAN_SCHEMA_HINT = {
    "actions": [
        {
            "type": "add_node|add_text|write_heading|write_definition|write_bullet_list|"
            "write_equation|draw_diagram|create_coordinate_system|plot_function|annotate|highlight",
            "id": "optional_stable_id",
            "label": "text/term/heading",
            "sublabel": "optional detail",
            "text": "optional body",
            "latex": "optional equation latex",
            "items": ["optional", "bullets"],
            "region": "left|right|top|bottom|center|bottom_left|bottom_right",
            "relative_to": "optional_existing_element_id",
            "target": "existing_element_id for annotate/highlight",
            "targets": ["existing_element_ids"],
            "style": "box|underline|pulse|circle",
        }
    ],
    "triggers": [
        {
            "phrase": "spoken phrase to match",
            "target": "logical_or_real_element_id",
            "action": "highlight|pulse|underline|circle|annotation",
            "cooldown_ms": 3000,
            "max_fires": 3,
        }
    ],
    "focus": {"target_ids": ["ids"], "priority": "high|normal"},
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
        # Derive covered vs pending from plan status when available.
        ctx["covered_concepts"] = [c.get("id") for c in concepts if c.get("status") == "completed"]
        ctx["pending_concepts"] = [c.get("id") for c in concepts if c.get("status") != "completed"]
    ckpt = (
        await db.execute(select(LessonCheckpoint).where(LessonCheckpoint.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if ckpt is not None and isinstance(ckpt.state, dict):
        st = ckpt.state
        # Prefer graph's view when present (authoritative resume index).
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
        # Strip fenced blocks.
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
    """Call Qwen (OpenAI-compatible) and return the parsed plan dict."""
    settings = get_settings()
    api_key = settings.effective_board_director_api_key
    if not api_key:
        raise IntentHandlingError(
            "Board director model is not configured", code="qwen_unavailable", retryable=False
        )
    url = settings.board_director_base_url.rstrip("/") + "/chat/completions"
    user_payload = {
        "board_summary": context.board_summary,
        "lesson_state": context.lesson_state,
        "recent_transcript": context.transcript,
        "visual_intent": context.intent,
        "focus": context.focus,
        "region_hint": context.region_hint,
        "supported_action_types": sorted(SUPPORTED_ACTION_TYPES),
        "response_schema_hint": _PLAN_SCHEMA_HINT,
    }
    body = {
        "model": settings.board_director_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    timeout = min(float(settings.board_director_timeout_seconds), MODEL_TIMEOUT_SECONDS)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
        t1 = time.perf_counter()
        logger.info(
            "board_director request_ok duration_ms=%.1f status=%s",
            (t1 - t0) * 1000,
            response.status_code,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
        logger.warning("board_director timeout: %s", exc)
        raise IntentHandlingError("Board director timed out", code="qwen_unavailable", retryable=True) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("board_director http_error status=%s", exc.response.status_code)
        raise IntentHandlingError(
            f"Board director returned HTTP {exc.response.status_code}",
            code="qwen_unavailable",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("board_director request_failed: %s", exc)
        raise IntentHandlingError("Board director request failed", code="qwen_unavailable", retryable=True) from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("board_director bad_response_shape: %s", exc)
        raise IntentHandlingError("Board director response shape was invalid", retryable=True) from exc

    try:
        plan = _extract_json(content if isinstance(content, str) else json.dumps(content))
    except Exception as exc:
        logger.warning("board_director json_parse_failed: %s", exc)
        raise IntentHandlingError("Board director returned invalid JSON", retryable=True) from exc
    return plan


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the model plan; reject unsupported actions."""
    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise IntentHandlingError("Plan must contain an actions array", retryable=True)
    normalized: list[dict[str, Any]] = []
    for raw in actions[:MAX_PLAN_ACTIONS]:
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "").strip()
        if action_type not in SUPPORTED_ACTION_TYPES:
            logger.warning("board plan rejected unsupported action type=%s", action_type)
            raise IntentHandlingError(
                f"Unsupported board action: {action_type}", code="invalid_arguments", retryable=True
            )
        region = raw.get("region")
        if region is not None and str(region) not in REGION_HINTS and str(region) not in layout.REGION_RECTS:
            raw = {**raw, "region": None}
        normalized.append(dict(raw))
    focus = plan.get("focus") if isinstance(plan.get("focus"), dict) else {}
    triggers = plan.get("triggers")
    if not isinstance(triggers, list):
        triggers = []
    return {"actions": normalized, "focus": dict(focus), "triggers": triggers}


def _text_style_for(action: Mapping[str, Any]) -> str:
    if action.get("type") == "add_node":
        return "subheading" if action.get("sublabel") else "body"
    return "body"


def _action_to_tool_calls(action: Mapping[str, Any]) -> list[tuple[str, dict[str, Any], str | None]]:
    """Map one planned action to one or more (tool_name, args, logical_id) calls."""
    atype = str(action.get("type") or "")
    logical_id = str(action.get("id") or "") or None
    position = action.get("position") if isinstance(action.get("position"), dict) else {"x": 0.1, "y": 0.1}
    size = action.get("size") if isinstance(action.get("size"), dict) else None

    if atype in {"add_node", "add_text", "write_text"}:
        label = _clean_text(action.get("label") or action.get("text"), 2000)
        sub = _clean_text(action.get("sublabel"), 500)
        text = f"{label}\n{sub}".strip() if sub else label
        if not text:
            return []
        args: dict[str, Any] = {"text": text, "position": position, "style": _text_style_for(action)}
        if size:
            args["size"] = size
        return [("board_write_text", args, logical_id)]

    if atype == "write_heading":
        text = _clean_text(action.get("label") or action.get("text"), 400)
        if not text:
            return []
        return [("board_write_heading", {"text": text, "position": position, "level": 2}, logical_id)]

    if atype == "write_definition":
        term = _clean_text(action.get("label") or action.get("term"), 400)
        body = _clean_text(action.get("text") or action.get("sublabel"), 2000)
        if not term or not body:
            # degrade to text block rather than failing the whole plan
            text = (term + (": " if term and body else "") + body).strip()
            if not text:
                return []
            return [("board_write_text", {"text": text, "position": position, "style": "definition"}, logical_id)]
        return [("board_write_definition", {"term": term, "text": body, "position": position}, logical_id)]

    if atype == "write_bullet_list":
        items = action.get("items")
        if not isinstance(items, list) or not items:
            body = _clean_text(action.get("text") or action.get("label"), 2000)
            items = [line.strip("- •") for line in body.splitlines() if line.strip()] or ([body] if body else [])
        items = [_clean_text(str(i), 300) for i in items if str(i).strip()][:20]
        if not items:
            return []
        return [("board_write_bullet_list", {"items": items, "position": position}, logical_id)]

    if atype == "write_equation":
        latex = _clean_text(action.get("latex") or action.get("label") or action.get("text"), 1200)
        if not latex:
            return []
        return [("board_write_equation", {"latex": latex, "position": position}, logical_id)]

    if atype == "draw_diagram":
        nodes = action.get("nodes") if isinstance(action.get("nodes"), list) else []
        edges = action.get("edges") if isinstance(action.get("edges"), list) else []
        # Minimal normalization: ensure required node fields exist.
        norm_nodes = []
        for idx, node in enumerate(nodes[:20]):
            if not isinstance(node, dict):
                continue
            nid = _clean_text(node.get("id") or f"n{idx+1}", 64) or f"n{idx+1}"
            pos = node.get("position") if isinstance(node.get("position"), dict) else {"x": 0.1 + idx * 0.08, "y": 0.2}
            norm_nodes.append({"id": nid, "label": _clean_text(node.get("label"), 200), "position": pos})
        if not norm_nodes:
            # Can't invent a diagram safely; fall back to a text block.
            label = _clean_text(action.get("label") or action.get("text"), 400)
            return [("board_write_text", {"text": label or "Diagram", "position": position}, logical_id)] if label or True else []
        norm_edges = []
        node_ids = {n["id"] for n in norm_nodes}
        for edge in edges[:40]:
            if not isinstance(edge, dict):
                continue
            src, tgt = edge.get("from") or edge.get("source"), edge.get("to") or edge.get("target")
            if src in node_ids and tgt in node_ids and src != tgt:
                norm_edges.append({"from": src, "to": tgt, "label": _clean_text(edge.get("label"), 200)})
        args = {"kind": "generic", "nodes": norm_nodes, "edges": norm_edges}
        if action.get("label"):
            args["title"] = _clean_text(action.get("label"), 300)
        return [("board_draw_diagram", args, logical_id)]

    if atype == "draw_template":
        template_id = _clean_text(action.get("template_id"), 64)
        if not template_id or template_id not in VALID_TEMPLATE_IDS:
            # Unknown template: degrade to a text block rather than failing.
            label = _clean_text(action.get("label") or action.get("text"), 400)
            if not label:
                return []
            return [("board_write_text", {"text": label, "position": position, "style": "definition"}, logical_id)]
        region = action.get("region")
        if region is not None and str(region) not in REGION_HINTS:
            region = None
        labels = action.get("labels")
        if not isinstance(labels, list):
            labels = []
        labels = [_clean_text(str(l), 300) for l in labels if str(l).strip()][:20]
        args: dict[str, Any] = {
            "template_id": template_id,
            "region": region,
            "labels": labels,
            "animate": True,
        }
        if isinstance(action.get("position"), dict):
            args["position"] = action["position"]
        if isinstance(action.get("size"), dict):
            args["size"] = action["size"]
        return [("board_draw_template", args, logical_id)]

    if atype in {"annotate", "highlight"}:
        targets: list[str] = []
        if isinstance(action.get("targets"), list):
            targets = [str(t) for t in action.get("targets") if t]
        elif action.get("target"):
            targets = [str(action.get("target"))]
        targets = targets[:20]
        if not targets:
            return []
        style = str(action.get("style") or "box")
        if style == "pulse":  # not a native highlight style; map to circle
            style = "circle"
        if style not in {"underline", "circle", "box", "strike", "arrow"}:
            style = "box"
        args = {"target_ids": targets, "style": style, "color": "yellow"}
        if action.get("label"):
            args["label"] = _clean_text(action.get("label"), 300)
        return [("board_highlight", args, None)]

    # Graph actions: only allow if required ids are present; otherwise drop safely.
    if atype == "create_coordinate_system":
        args = {
            "position": position,
            "size": size or {"w": 0.34, "h": 0.28},
            "x_range": action.get("x_range") or [-10, 10],
            "y_range": action.get("y_range") or [-10, 10],
        }
        return [("board_create_coordinate_system", args, logical_id)]
    if atype == "plot_function":
        csid = action.get("coordinate_system_id") or action.get("target")
        expr = action.get("expression") or action.get("label")
        if not csid or not expr:
            return []
        return [("board_plot_function", {"coordinate_system_id": str(csid), "expression": str(expr)}, logical_id)]
    if atype == "add_graph_label":
        csid = action.get("coordinate_system_id") or action.get("target")
        text = action.get("text") or action.get("label")
        at = action.get("at") or {"x": 0, "y": 0}
        if not csid or not text:
            return []
        return [("board_add_graph_label", {"coordinate_system_id": str(csid), "at": at, "text": str(text)}, logical_id)]
    if atype == "draw_arrow":
        start = action.get("start") or position
        end = action.get("end") or {"x": min(float(position.get("x", 0.1)) + 0.2, 0.95), "y": float(position.get("y", 0.1))}
        args = {"start": start, "end": end}
        if action.get("label"):
            args["label"] = _clean_text(action.get("label"), 200)
        return [("board_draw_arrow", args, logical_id)]

    return []


class BoardIntentHandler:
    """Orchestrates intent -> plan -> layout -> board events.

    Optionally registers transcript triggers (from the Board Director plan)
    so that spoken phrases later trigger visual emphasis on board elements.
    """

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
        lesson_state = await fetch_lesson_context(db, lesson_id)
        transcript = await fetch_recent_transcript(db, lesson_id)
        context = IntentContext(
            board_summary=board_summary,
            lesson_state=lesson_state,
            transcript=transcript,
            intent=intent,
            focus=focus,
            region_hint=str(region_hint) if region_hint else None,
        )

        t_model = time.perf_counter()
        try:
            plan = await call_board_director(context)
        finally:
            logger.info("board_director total_ms=%.1f lesson=%s", (time.perf_counter() - t_model) * 1000, lesson_id)
        try:
            plan = validate_plan(plan)
        except IntentHandlingError:
            logger.warning("board_intent schema_validation_failed lesson=%s", lesson_id)
            raise

        laid_out = layout.layout_plan(plan, document=document, default_region=context.region_hint)
        executed: list[dict[str, Any]] = []
        id_map: dict[str, str] = {}  # logical id -> created element_id

        for action in laid_out.get("actions", []):
            # Resolve relative_to logical ids if the model referenced a just-created one.
            rel = action.get("relative_to")
            if isinstance(rel, str) and rel in id_map:
                action = {**action, "relative_to": id_map[rel]}
            # Resolve coordinate_system_id references from logical ids.
            if isinstance(action.get("coordinate_system_id"), str) and action["coordinate_system_id"] in id_map:
                action = {**action, "coordinate_system_id": id_map[action["coordinate_system_id"]]}
            if isinstance(action.get("target"), str) and action["target"] in id_map:
                action = {**action, "target": id_map[action["target"]]}

            tool_calls = _action_to_tool_calls(action)
            for tool_name, args, logical_id in tool_calls:
                try:
                    result = await self._registry.execute_structured(
                        db, lesson_id, tool_name, args, source="system"
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("board_intent action_exec_failed tool=%s err=%s", tool_name, exc)
                    result = {"ok": False, "error": "internal_error", "message": str(exc), "retryable": False}
                executed.append({"tool": tool_name, "result": result, "logical_id": logical_id})
                if result.get("ok") and logical_id and result.get("element_id"):
                    id_map[logical_id] = str(result["element_id"])
                if not result.get("ok"):
                    logger.warning(
                        "board_intent action_rejected lesson=%s tool=%s error=%s",
                        lesson_id,
                        tool_name,
                        result.get("error"),
                    )

        successes = [e for e in executed if (e.get("result") or {}).get("ok")]
        logger.info(
            "board_intent completed lesson=%s actions=%d ok=%d",
            lesson_id,
            len(executed),
            len(successes),
        )
        # -- transcript trigger registration ---------------------------------
        registered_triggers: list[dict[str, Any]] = []
        trigger_system = self._trigger_system
        if trigger_system is not None:
            plan_triggers = plan.get("triggers")
            if isinstance(plan_triggers, list):
                for raw in plan_triggers:
                    if not isinstance(raw, dict):
                        continue
                    phrase = str(raw.get("phrase") or "").strip()
                    target = str(raw.get("target") or "").strip()
                    action = str(raw.get("action") or "highlight").strip().lower()
                    if not phrase or not target or action not in SUPPORTED_TRIGGER_ACTIONS:
                        continue
                    # Remap logical target id to the real element id if created.
                    real_target = id_map.get(target, target)
                    cooldown_ms = raw.get("cooldown_ms")
                    try:
                        cooldown_ms = int(cooldown_ms) if cooldown_ms is not None else DEFAULT_COOLDOWN_MS
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
                        registered_triggers.append(
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
                            "board_intent trigger registration failed lesson=%s: %s",
                            lesson_id,
                            exc,
                        )
        # --------------------------------------------------------------------
        focus_out = laid_out.get("focus") or {}
        # Remap focus target ids through logical id map when possible.
        if isinstance(focus_out, dict) and isinstance(focus_out.get("target_ids"), list):
            focus_out = {
                **focus_out,
                "target_ids": [id_map.get(t, t) for t in focus_out.get("target_ids", [])],
            }
        return {
            "ok": True,
            "intent": intent,
            "actions_executed": len(successes),
            "actions": executed,
            "focus": focus_out,
            "triggers_registered": registered_triggers,
        }


__all__ = ["BoardIntentHandler", "IntentHandlingError", "summarize_board", "validate_plan"]
