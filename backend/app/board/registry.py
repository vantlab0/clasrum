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

        # Realtime flow control: the hub handles the pause locally; the registry
        # only needs to acknowledge it so non-realtime callers stay valid too.
        if name == "wait_for_student":
            return {
                "ok": True,
                "waiting_for_student": True,
                "message": "Paused; listening for the student's answer.",
            }

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

        if spec.name == "create_board_visual":
            return await self._execute_create_board_visual(
                db, lesson_id, spec, args, source=source, animate=animate
            )
        if spec.kind == "board":
            return await self._execute_board(
                db, lesson_id, spec, args, source=source, animate=animate
            )
        return await self._execute_lesson(db, lesson_id, spec, args)

    # -----------------------------------------------------------------------
    # Internal handler for create_board_visual: delegates to the board director
    # (Qwen3.7-Flash) and persists the drawing plan as a draw_plan event.
    # -----------------------------------------------------------------------
    async def _execute_create_board_visual(
        self,
        db: AsyncSession,
        lesson_id: str,
        spec: ToolSpec,
        args: CreateBoardVisualArgs,
        *,
        source: str,
        animate: bool,
    ) -> dict[str, Any]:
        from app.board import layout
        from app.board.intent_handler import (
            BoardIntentHandler,
            IntentContext,
            IntentHandlingError,
            call_board_director,
            fetch_lesson_context,
            fetch_recent_transcript,
            fit_plan_to_region,
            plan_total_duration_ms,
            summarize_board,
            validate_drawing_plan,
        )
        from app.board.layout import LayoutNoSpaceError

        name = spec.name
        intent = str(getattr(args, "instruction", "") or "").strip()[:1000]
        if not intent:
            return {"ok": False, "error": "invalid_arguments", "message": "intent is required", "retryable": True}
        focus = "new_content"
        # ``related_concept`` is a concept id, not a screen region.  Region hints
        # come from the caller's explicit context when present.
        context_hint = args.context.get("region_hint") if isinstance(args.context, dict) else None
        region_hint = context_hint if str(context_hint) in layout.REGION_RECTS else None

        logger.info(
            "create_board_visual received lesson=%s visual_type=%s complexity=%s intent=%s",
            lesson_id,
            args.visual_type,
            args.complexity,
            intent[:120],
        )

        try:
            document = await self._board.reconstruct_document(db, lesson_id)
        except Exception:
            document = {"elements": [], "last_sequence": 0}
        board_summary = summarize_board(document)
        lesson_state = await fetch_lesson_context(db, lesson_id)
        transcript = await fetch_recent_transcript(db, lesson_id)

        t0 = __import__("time").perf_counter()
        try:
            raw_plan = await call_board_director(
                IntentContext(
                    board_summary=board_summary,
                    lesson_state=lesson_state,
                    transcript=transcript,
                    intent=intent,
                    focus=focus,
                    region_hint=str(region_hint) if region_hint else None,
                )
            )
        except IntentHandlingError as exc:
            logger.warning("create_board_visual director failed lesson=%s: %s", lesson_id, exc.message)
            return exc.to_tool_result()
        duration_ms = (__import__("time").perf_counter() - t0) * 1000
        logger.info(
            "create_board_visual director total_ms=%.1f lesson=%s", duration_ms, lesson_id,
        )

        # Validate + repair the drawing plan.
        try:
            validated = validate_drawing_plan(raw_plan, repair=True)
        except IntentHandlingError as exc:
            logger.warning(
                "create_board_visual plan validation failed lesson=%s: %s", lesson_id, exc.message
            )
            return exc.to_tool_result()

        # Fit the plan into an available board region (no full-board overwrite).
        # The board may have changed while the director was generating, so the
        # document is re-read under the layout lock immediately before placement.
        from app.board.drawing_protocol import plan_element_bounds
        from app.board.layout import BlackboardLayoutManager, board_layout_lock

        boxes = plan_element_bounds(validated)
        if boxes:
            min_x = min(b[0] for b in boxes)
            min_y = min(b[1] for b in boxes)
            max_x = max(b[0] + b[2] for b in boxes)
            max_y = max(b[1] + b[3] for b in boxes)
            plan_w = max(0.1, max_x - min_x)
            plan_h = max(0.1, max_y - min_y)
        else:
            plan_w, plan_h = 0.52, 0.36

        async with board_layout_lock(lesson_id):
            try:
                document = await self._board.reconstruct_document(db, lesson_id)
            except Exception:
                document = {"elements": [], "last_sequence": 0}
            manager = BlackboardLayoutManager(document)
            preferred_region = str(region_hint) if region_hint else None
            try:
                _, region_rect = manager.find_region_for_visual(
                    width=plan_w, height=plan_h, preferred=preferred_region
                )
            except LayoutNoSpaceError as exc:
                logger.warning("create_board_visual no space lesson=%s", lesson_id)
                return {
                    "ok": False,
                    "error": exc.code,
                    "message": str(exc),
                    "retryable": True,
                }
            region = (region_rect.x, region_rect.y, region_rect.w, region_rect.h)
            plan_payload = fit_plan_to_region(validated, region)

            payload = {
                **plan_payload,
                "context": {
                    "topic": lesson_state.get("topic"),
                    "subject": lesson_state.get("subject"),
                    "current_concept": lesson_state.get("current_concept")
                    or lesson_state.get("current_concept_title"),
                    "region": region_hint or "full",
                    "intent": intent,
                    "visual_type": args.visual_type,
                    "complexity": args.complexity,
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

        id_map = {
            element["id"]: record.element_id
            for element in plan_payload.get("elements", [])
            if isinstance(element, dict) and element.get("id")
        }
        # Register transcript triggers and resolve focus via the BoardIntentHandler.
        handler = BoardIntentHandler(
            board=self._board,
            registry=self,
            trigger_system=self._trigger_system,
        )
        registered_triggers = await handler._register_triggers(
            db, lesson_id, plan_payload, id_map,
        )
        focus_out = BoardIntentHandler._resolve_focus(plan_payload, id_map)

        logger.info(
            "create_board_visual completed lesson=%s elements=%d duration_ms=%d",
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

        from app.board.layout import (
            LayoutNoSpaceError,
            board_layout_lock,
        )

        # Allocation and commit are one transaction per lesson: two concurrent
        # tool calls can never be given the same free region.
        async with board_layout_lock(lesson_id):
            # Direct board writing: deterministic layout, no AI coordinates.
            if spec.name == "board_write_on_board" or payload.get("position_hint"):
                try:
                    document = await self._board.reconstruct_document(db, lesson_id)
                except Exception:
                    document = {"elements": []}
                from app.board.layout import BlackboardLayoutManager

                manager = BlackboardLayoutManager(document)
                try:
                    payload = manager.place_text(payload)
                except LayoutNoSpaceError as exc:
                    raise BoardValidationError(exc.code, str(exc), retryable=True) from None

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
