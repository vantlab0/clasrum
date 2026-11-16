"""Tool registry: the *only* function surface exposed to the tutor model.

Authority: ``docs/CONTRACTS.md`` §4. Exactly **28 tools** exist — 22 board tools
that each map to exactly one board event type, plus 6 lesson tools that delegate
to :mod:`app.graph.service` lesson actions. Nothing else is reachable: there is
no eval, no SQL, no filesystem, no shell and no arbitrary HTTP tool.

Execution contract (:meth:`ToolRegistry.execute`):

* arguments arrive as a JSON string (Qwen function-call arguments) or a dict and
  are validated twice — JSON parse, then a closed pydantic model per tool;
* invalid arguments / unknown elements / bad targets return
  ``{"ok": false, "error": ..., "message": ..., "retryable": ...}`` with **zero**
  DB mutation (validation happens before the append), and are logged at WARNING;
* a successful board tool returns
  ``{"ok": true, "event_id": ..., "sequence": ..., "element_id": ...}``;
* a successful lesson tool returns ``{"ok": true, "lesson_state": {...}}``;
* results never contain secrets, transcripts or raw DB rows.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.board import sampling
from app.board.schemas import (
    ALL_TOOL_NAMES,
    BOARD_TOOL_EVENT_TYPES,
    BOARD_TOOL_NAMES,
    INTENT_TOOL_NAMES,
    INTERNAL_BOARD_TOOL_NAMES,
    LESSON_TOOL_NAMES,
    REALTIME_TOOL_NAMES,
    TOOL_SPECS,
    TOOL_SPECS_BY_NAME,
    ToolSpec,
    openai_tool_schemas,
    public_openai_tool_schemas,
)
from app.board.service import (
    BoardError,
    BoardService,
    BoardValidationError,
    EVENT_CREATE_COORDINATE_SYSTEM,
    EVENT_PLOT_FUNCTION,
)
from app.db.constants import BoardEventSource
from app.realtime.transcript_triggers import TranscriptTriggerSystem

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 4000


@runtime_checkable
class LessonStatePublisher(Protocol):
    """Sink for lesson-state / teaching events (implemented by the WS hub)."""

    async def publish_lesson_state(self, lesson_id: str, lesson_state: dict[str, Any]) -> None:
        ...

    async def publish_lesson_event(
        self, lesson_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        ...


def _json_dumps(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(text) > MAX_TOOL_RESULT_CHARS:  # pragma: no cover - results are tiny by construction
        return json.dumps(
            {
                "ok": False,
                "error": "result_too_large",
                "message": "tool result exceeded the maximum size",
                "retryable": False,
            },
            separators=(",", ":"),
        )
    return text


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic error into a short, model-actionable message."""
    parts: list[str] = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in error.get("loc", ()) if str(item) != "__root__")
        message = str(error.get("msg", "invalid value"))
        message = message.replace("Input should be", "must be")
        parts.append(f"{location}: {message}" if location else message)
    text = "; ".join(parts)
    return text[:512] if text else "invalid arguments"


def _invalid_arguments(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "invalid_arguments",
        "message": message,
        "retryable": True,
    }


class ToolRegistry:
    """Name -> spec + handler mapping for the 28 tutor tools."""

    def __init__(
        self,
        board: BoardService | None = None,
        *,
        lesson_publisher: LessonStatePublisher | None = None,
        trigger_system: TranscriptTriggerSystem | None = None,
    ) -> None:
        self._board = board or BoardService()
        self._lesson_publisher = lesson_publisher
        self._trigger_system = trigger_system

    # -- introspection -----------------------------------------------------
    @property
    def board(self) -> BoardService:
        return self._board

    def set_lesson_publisher(self, publisher: LessonStatePublisher | None) -> None:
        self._lesson_publisher = publisher

    @property
    def tool_names(self) -> tuple[str, ...]:
        return ALL_TOOL_NAMES

    @property
    def board_tool_names(self) -> tuple[str, ...]:
        return BOARD_TOOL_NAMES

    @property
    def lesson_tool_names(self) -> tuple[str, ...]:
        return LESSON_TOOL_NAMES

    def has_tool(self, name: str) -> bool:
        return name in TOOL_SPECS_BY_NAME

    def get_spec(self, name: str) -> ToolSpec | None:
        return TOOL_SPECS_BY_NAME.get(name)

    def event_type_for(self, name: str) -> str | None:
        spec = TOOL_SPECS_BY_NAME.get(name)
        return spec.event_type if spec is not None else None

    def openai_tools(self) -> list[dict[str, Any]]:
        """All registered tool schemas (internal + public)."""
        return openai_tool_schemas()

    def public_openai_tools(self) -> list[dict[str, Any]]:
        """Schemas exposed to the realtime conversational model."""
        return public_openai_tool_schemas()

    def specs(self) -> list[dict[str, Any]]:
        return self.openai_tools()

    # -- execution ---------------------------------------------------------
    async def execute(
        self,
        db: AsyncSession,
        lesson_id: str,
        name: str,
        arguments: str | Mapping[str, Any] | None = None,
        *,
        source: str = BoardEventSource.QWEN_TOOL,
        animate: bool = True,
    ) -> str:
        """Validate + execute one tool call; always returns compact JSON."""
        try:
            result = await self._execute(
                db,
                lesson_id,
                name,
                arguments,
                source=source,
                animate=animate,
            )
        except BoardError as exc:
            logger.warning(
                "tool rejected lesson=%s tool=%s code=%s message=%s",
                lesson_id,
                name,
                exc.code,
                exc.message,
            )
            result = exc.to_tool_result()
        except Exception:  # pragma: no cover - unexpected bugs must not kill the socket
            logger.exception("tool execution failed lesson=%s tool=%s", lesson_id, name)
            result = {
                "ok": False,
                "error": "internal_error",
                "message": "the board operation could not be completed",
                "retryable": False,
            }
        return _json_dumps(result)

    async def execute_structured(
        self,
        db: AsyncSession,
        lesson_id: str,
        name: str,
        arguments: str | Mapping[str, Any] | None = None,
        *,
        source: str = BoardEventSource.QWEN_TOOL,
        animate: bool = True,
    ) -> dict[str, Any]:
        """Same as :meth:`execute` but returns the result dict (used by tests/hub)."""
        raw = await self.execute(
            db, lesson_id, name, arguments, source=source, animate=animate
        )
        return json.loads(raw)

    async def _execute(
        self,
        db: AsyncSession,
        lesson_id: str,
        name: str,
        arguments: str | Mapping[str, Any] | None,
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        spec = TOOL_SPECS_BY_NAME.get(name)
        if spec is None:
            logger.warning("tool rejected lesson=%s tool=%s code=unknown_tool", lesson_id, name)
            return {
                "ok": False,
                "error": "unknown_tool",
                "message": (
                    f"unknown tool {name!r}; only the registered classroom tools "
                    "may be called"
                ),
                "retryable": False,
            }

        parsed_arguments = self._parse_arguments(arguments)
        if parsed_arguments is None:
            logger.warning("tool rejected lesson=%s tool=%s code=invalid_json", lesson_id, name)
            return _invalid_arguments("arguments must be a JSON object")

        # Intent tools and dual-use board_highlight (intent vs internal args).
        if spec.kind == "intent" or name == "board_highlight":
            # board_highlight (kind board) may be called with internal args.
            if name == "board_highlight" and "target_ids" in parsed_arguments and "target" not in parsed_arguments:
                return await self._execute_internal_highlight(
                    db, lesson_id, parsed_arguments, source=source, animate=animate
                )
            if spec.kind == "intent" or name == "board_highlight":
                return await self._execute_intent(
                    db, lesson_id, spec, parsed_arguments, source=source, animate=animate
                )

        try:
            args = spec.args_model.model_validate(parsed_arguments)
        except ValidationError as exc:
            message = _format_validation_error(exc)
            logger.warning(
                "tool rejected lesson=%s tool=%s code=invalid_arguments message=%s",
                lesson_id,
                name,
                message,
            )
            return _invalid_arguments(message)

        if spec.kind == "board":
            return await self._execute_board(
                db, lesson_id, spec, args, source=source, animate=animate
            )
        return await self._execute_lesson(db, lesson_id, spec, args)

    async def _execute_intent(
        self,
        db: AsyncSession,
        lesson_id: str,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        from app.board.intent_handler import BoardIntentHandler, IntentHandlingError

        name = spec.name
        try:
            # board_highlight is dual-use: internal low-level calls carry target_ids.
            if name == "board_highlight":
                if "target_ids" in arguments and "target" not in arguments:
                    return await self._execute_internal_highlight(
                        db, lesson_id, arguments, source=source, animate=animate
                    )
                # Intent form: target (+ style) -> resolve to existing element(s).
                return await self._execute_highlight_intent(
                    db, lesson_id, arguments, source=source, animate=animate
                )

            if name == "board_clear_region":
                try:
                    args = spec.args_model.model_validate(arguments)
                except ValidationError as exc:
                    return _invalid_arguments(_format_validation_error(exc))
                return await self._execute_clear_region_intent(
                    db, lesson_id, args, source=source, animate=animate
                )

            if name == "board_intent":
                try:
                    args = spec.args_model.model_validate(arguments)
                except ValidationError as exc:
                    return _invalid_arguments(_format_validation_error(exc))
                handler = BoardIntentHandler(
                    board=self._board,
                    registry=self,
                    trigger_system=self._trigger_system,
                )
                try:
                    return await handler.handle(db, lesson_id, args.model_dump())
                except IntentHandlingError as exc:
                    logger.warning("board_intent failed lesson=%s: %s", lesson_id, exc.message)
                    return exc.to_tool_result()

            return {
                "ok": False,
                "error": "unknown_tool",
                "message": f"intent tool {name!r} is not supported",
                "retryable": False,
            }
        except Exception:  # pragma: no cover - intent failures must not kill the socket
            logger.exception("intent tool execution failed lesson=%s tool=%s", lesson_id, name)
            return {
                "ok": False,
                "error": "internal_error",
                "message": "the board operation could not be completed",
                "retryable": False,
            }

    async def _execute_internal_highlight(
        self,
        db: AsyncSession,
        lesson_id: str,
        arguments: dict[str, Any],
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        from app.board.schemas import HighlightArgs

        try:
            args = HighlightArgs.model_validate(arguments)
        except ValidationError as exc:
            return _invalid_arguments(_format_validation_error(exc))
        spec = ToolSpec(
            name="board_highlight",
            kind="board",
            event_type="highlight",
            target_mode="new",
            args_model=HighlightArgs,
            description="internal highlight",
            allowed_element_types=(),
        )
        return await self._execute_board(db, lesson_id, spec, args, source=source, animate=animate)

    async def _resolve_highlight_target(self, db: AsyncSession, lesson_id: str, target: str) -> list[str]:
        document = await self._board.reconstruct_document(db, lesson_id)
        needle = str(target).strip()
        if not needle:
            return []
        # Exact element id match first.
        for element in document.get("elements", []) or []:
            if element.get("element_id") == needle and element.get("removed_at_sequence") is None:
                return [needle]
        # Fallback: case-insensitive label/text contains match.
        lowered = needle.lower()
        for element in document.get("elements", []) or []:
            if element.get("removed_at_sequence") is not None:
                continue
            payload = element.get("payload") or {}
            for key in ("text", "label", "term", "title", "latex"):
                val = payload.get(key)
                if isinstance(val, str) and (lowered == val.lower() or lowered in val.lower()):
                    return [str(element.get("element_id"))]
        return []

    async def _execute_highlight_intent(
        self,
        db: AsyncSession,
        lesson_id: str,
        arguments: dict[str, Any],
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        from app.board.schemas import BoardHighlightIntentArgs, HighlightArgs

        try:
            intent = BoardHighlightIntentArgs.model_validate(arguments)
        except ValidationError as exc:
            return _invalid_arguments(_format_validation_error(exc))
        target_ids = await self._resolve_highlight_target(db, lesson_id, intent.target)
        if not target_ids:
            return {
                "ok": False,
                "error": "unknown_element",
                "message": f"could not find a board element matching {intent.target!r}",
                "retryable": True,
            }
        style = intent.style
        if style == "pulse":
            style = "circle"
        internal = HighlightArgs.model_validate({"target_ids": target_ids, "style": style, "color": "yellow"})
        spec = ToolSpec(
            name="board_highlight",
            kind="board",
            event_type="highlight",
            target_mode="new",
            args_model=HighlightArgs,
            description="internal highlight",
            allowed_element_types=(),
        )
        return await self._execute_board(db, lesson_id, spec, internal, source=source, animate=animate)

    async def _execute_clear_region_intent(
        self,
        db: AsyncSession,
        lesson_id: str,
        args: Any,
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        from app.board.schemas import ClearArgs, EraseArgs

        if args.scope == "all":
            spec = ToolSpec(
                name="board_clear",
                kind="board",
                event_type="clear",
                target_mode="none",
                args_model=ClearArgs,
                description="clear board",
                allowed_element_types=(),
            )
            return await self._execute_board(db, lesson_id, spec, ClearArgs(), source=source, animate=animate)

        # Region clear: map named region to a normalized rect for erase.
        rects = {
            "left": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
            "right": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
            "top": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5},
            "bottom": {"x": 0.0, "y": 0.5, "w": 1.0, "h": 0.5},
            "center": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
            "bottom_left": {"x": 0.0, "y": 0.5, "w": 0.5, "h": 0.5},
            "bottom_right": {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5},
        }
        region = rects.get(str(args.region))
        if region is None:
            return _invalid_arguments("unsupported region for board_clear_region")
        erase = EraseArgs.model_validate({"scope": "region", "region": region})
        spec = ToolSpec(
            name="board_erase",
            kind="board",
            event_type="erase",
            target_mode="new",
            args_model=EraseArgs,
            description="erase region",
            allowed_element_types=(),
        )
        return await self._execute_board(db, lesson_id, spec, erase, source=source, animate=animate)

    @staticmethod
    def _parse_arguments(
        arguments: str | Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if arguments is None:
            return {}
        if isinstance(arguments, Mapping):
            return dict(arguments)
        if isinstance(arguments, (bytes, bytearray)):
            try:
                arguments = arguments.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if not isinstance(arguments, str):
            return None
        text = arguments.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    async def _execute_board(
        self,
        db: AsyncSession,
        lesson_id: str,
        spec: ToolSpec,
        args: Any,
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        event_type = spec.event_type
        assert event_type is not None  # board specs always map to an event type
        payload = args.build_payload()
        element_id = getattr(args, "element_id", None)

        if event_type == EVENT_PLOT_FUNCTION:
            payload = await self._prepare_plot_function(db, lesson_id, args, payload)

        record = await self._board.append_event(
            db,
            lesson_id,
            event_type,
            payload,
            element_id=element_id,
            source=source,
            animate=animate,
            allowed_element_types=spec.allowed_element_types or None,
        )
        return record.to_tool_result()

    async def _prepare_plot_function(
        self, db: AsyncSession, lesson_id: str, args: Any, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve the sampling domain and compute ``samples`` safely."""
        document = await self._board.reconstruct_document(db, lesson_id)
        coordinate_system = next(
            (
                element
                for element in document.get("elements", [])
                if element["element_id"] == payload["coordinate_system_id"]
                and element.get("removed_at_sequence") is None
            ),
            None,
        )
        if coordinate_system is None:
            # The append step raises the canonical unknown_element error.
            return payload
        if coordinate_system.get("type") != EVENT_CREATE_COORDINATE_SYSTEM:
            return payload
        axis_range = (coordinate_system.get("payload") or {}).get("x_range") or [-10.0, 10.0]
        domain = payload.get("x_domain") or [float(axis_range[0]), float(axis_range[1])]
        x_min, x_max = float(domain[0]), float(domain[1])
        if x_min > x_max:
            x_min, x_max = x_max, x_min
        try:
            samples = sampling.sample_function(payload["expression"], x_min, x_max)
        except sampling.ExpressionError as exc:
            raise BoardValidationError(exc.code, str(exc), retryable=True) from None
        payload["x_domain"] = [round(x_min, 6), round(x_max, 6)]
        payload["samples"] = samples
        return payload

    async def _execute_lesson(
        self, db: AsyncSession, lesson_id: str, spec: ToolSpec, args: Any
    ) -> dict[str, Any]:
        # Imported lazily: graph.service imports board.service, and keeping the
        # registry import-light avoids a cycle at module import time.
        from app.graph import service as graph_service

        lesson_state = await graph_service.run_lesson_tool(
            db, lesson_id, spec.name, args.model_dump(exclude_none=True)
        )
        publisher = self._lesson_publisher
        if publisher is not None:
            try:
                await publisher.publish_lesson_state(lesson_id, lesson_state)
            except Exception:  # pragma: no cover - publishing must not fail a tool call
                logger.warning(
                    "lesson state publish failed lesson=%s tool=%s", lesson_id, spec.name, exc_info=True
                )
        return {"ok": True, "lesson_state": lesson_state}


__all__ = [
    "LessonStatePublisher",
    "ToolRegistry",
    "TOOL_SPECS",
]
