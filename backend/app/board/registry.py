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
    LESSON_TOOL_NAMES,
    TOOL_SPECS,
    TOOL_SPECS_BY_NAME,
    ToolSpec,
    openai_tool_schemas,
)
from app.board.service import (
    BoardError,
    BoardService,
    BoardValidationError,
    EVENT_CREATE_COORDINATE_SYSTEM,
    EVENT_PLOT_FUNCTION,
)
from app.db.constants import BoardEventSource

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
    ) -> None:
        self._board = board or BoardService()
        self._lesson_publisher = lesson_publisher

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
        """The ``tools`` array for the Qwen ``session.update`` payload."""
        return openai_tool_schemas()

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
