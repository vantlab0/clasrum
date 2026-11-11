"""Board domain: the tutor's blackboard (event-sourced, replayable).

Public surface
--------------
``schemas``     pydantic v2 argument models + OpenAI tool schemas (28 tools)
``sampling``    sandboxed math expression evaluator used by ``plot_function``
``service``     transactional writer / snapshot manager / document reducer
``registry``    name -> spec + handler mapping used by Qwen and the WS hub
"""
from __future__ import annotations

from app.board.registry import ToolRegistry
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
    BOARD_EVENT_TYPES,
    BoardError,
    BoardEventPublisher,
    BoardEventRecord,
    BoardService,
    BoardValidationError,
    UnknownElementError,
    active_elements,
    apply_event_to_document,
    empty_document,
)

__all__ = [
    "ALL_TOOL_NAMES",
    "BOARD_EVENT_TYPES",
    "BOARD_TOOL_EVENT_TYPES",
    "BOARD_TOOL_NAMES",
    "BoardError",
    "BoardEventPublisher",
    "BoardEventRecord",
    "BoardService",
    "BoardValidationError",
    "LESSON_TOOL_NAMES",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "ToolRegistry",
    "ToolSpec",
    "UnknownElementError",
    "active_elements",
    "apply_event_to_document",
    "empty_document",
    "openai_tool_schemas",
]
