"""Transcript Trigger System: synchronizes visual emphasis with spoken explanation.

Maintains per-lesson triggers registered by the Board Director. When the tutor
speaks a registered phrase, the system fires a secondary board action
(highlight / pulse / underline / circle / annotation) targeting the associated
board element.

All matching is deterministic and local: no AI calls, no external dependencies,
no per-word model inference.

Responsibilities
----------------
* Maintain active triggers for each live lesson/session (isolated by lesson_id).
* Allow triggers to be registered by the Board Director (optional, contextual).
* Associate triggers with board element IDs.
* Receive partial and final transcript events.
* Match spoken phrases against registered triggers (case-insensitive,
  word-boundary-aware, punctuation-tolerant, multi-word capable).
* Generate secondary board actions through existing board events only.
* Prevent duplicate or excessive trigger execution (cooldowns + debouncing).
* Support trigger expiration, enabling/disabling, and cleanup on session end.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.board.registry import ToolRegistry

from app.board.service import BoardService, BoardValidationError
from app.config import get_settings
from app.db.constants import BoardEventSource, Speaker

logger = logging.getLogger(__name__)

#: Actions that can be represented using existing board events (board_highlight).
SUPPORTED_TRIGGER_ACTIONS: frozenset[str] = frozenset(
    {"highlight", "pulse", "underline", "circle", "annotation", "reveal"}
)

#: Map trigger action -> board_highlight style (existing board event).
_ACTION_TO_STYLE: dict[str, str] = {
    "highlight": "box",
    "pulse": "circle",
    "underline": "underline",
    "circle": "circle",
    "annotation": "box",
    # A reveal is a cue-gated draw_plan stage, not a visible annotation.
    "reveal": "reveal",
}

MAX_TRIGGERS_PER_LESSON = 50
DEFAULT_COOLDOWN_MS = 3000
#: Partials fire no more often than this (ms) to avoid flicker on streaming text.
PARTIAL_DEBOUNCE_MS = 800


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _now_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000.0


def _parse_iso_ms(value: str | None) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return None


def normalize_to_words(text: str) -> list[str]:
    """Lowercase, strip punctuation, collapse whitespace -> list of words.

    Punctuation is replaced with spaces so that ``ATP,`` matches ``ATP`` and
    ``mitochondria produce ATP.`` matches phrase ``produces ATP`` at the
    word level (``produce`` vs ``produces`` are different words, as intended).
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [w for w in text.split() if w]


def phrase_in_text(text_words: list[str], phrase_words: list[str]) -> bool:
    """True if *phrase_words* appear consecutively as whole words in *text_words*."""
    if not phrase_words:
        return False
    n = len(phrase_words)
    if n > len(text_words):
        return False
    for i in range(len(text_words) - n + 1):
        if text_words[i : i + n] == phrase_words:
            return True
    return False


@dataclass
class TranscriptTrigger:
    """One registered phrase -> board element -> board action mapping."""

    trigger_id: str
    lesson_id: str
    phrase: str
    target_id: str
    action: str
    cooldown_ms: int
    enabled: bool
    created_at: str
    max_fires: int | None = None
    fired_count: int = 0
    last_fired_at: str | None = None
    # Extra fields for cue-gated stage reveals (stage, target_ids, plan_element_id).
    payload: dict[str, Any] = field(default_factory=dict)
    # Normalized phrase words, computed once at construction for fast matching.
    _phrase_words: list[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._phrase_words = normalize_to_words(self.phrase)

    def matches(self, text: str) -> bool:
        if not self.enabled or not self._phrase_words:
            return False
        return phrase_in_text(normalize_to_words(text), self._phrase_words)

    def in_cooldown(self, now_ms: float) -> bool:
        if self.last_fired_at is None:
            return False
        last = _parse_iso_ms(self.last_fired_at)
        if last is None:
            return False
        return (now_ms - last) < self.cooldown_ms

    def can_fire(self, now_ms: float) -> bool:
        if not self.enabled:
            return False
        if self.max_fires is not None and self.fired_count >= self.max_fires:
            return False
        return not self.in_cooldown(now_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "lesson_id": self.lesson_id,
            "phrase": self.phrase,
            "target_id": self.target_id,
            "action": self.action,
            "cooldown_ms": self.cooldown_ms,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "max_fires": self.max_fires,
            "fired_count": self.fired_count,
            "last_fired_at": self.last_fired_at,
            "payload": dict(self.payload),
        }


class TranscriptTriggerSystem:
    """Per-lesson transcript trigger matching and board action execution.

    Triggers are isolated by ``lesson_id``. All matching is deterministic and
    local (no AI calls). Board actions are executed through the existing
    :class:`ToolRegistry` / :class:`BoardService`, so events are validated,
    persisted, and broadcast through the normal realtime infrastructure.
    """

    def __init__(
        self,
        board: BoardService | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._board = board or BoardService()
        if registry is None:
            # Lazy import to avoid a circular import with app.board.registry.
            from app.board.registry import ToolRegistry

            registry = ToolRegistry(board=self._board)
        self._registry = registry
        settings = get_settings()
        self._max_triggers = int(
            getattr(settings, "max_triggers_per_lesson", MAX_TRIGGERS_PER_LESSON)
        )
        # lesson_id -> trigger_id -> TranscriptTrigger
        self._triggers: dict[str, dict[str, TranscriptTrigger]] = {}
        # lesson_id -> accumulated partial transcript text
        self._partial_buffers: dict[str, str] = {}
        # lesson_id -> trigger_id -> last partial-match fire time (ms)
        self._partial_last_fired: dict[str, dict[str, float]] = {}

    # -- registration -------------------------------------------------------
    def register_trigger(
        self,
        lesson_id: str,
        phrase: str,
        target_id: str,
        action: str = "highlight",
        *,
        cooldown_ms: int | None = None,
        max_fires: int | None = None,
        trigger_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Register a transcript trigger.

        Returns the trigger id. Raises ``ValueError`` on invalid input or when
        the per-lesson trigger cap is reached (no eviction candidate available).
        """
        phrase = (phrase or "").strip()
        if not phrase:
            raise ValueError("phrase is required")
        target_id = (target_id or "").strip()
        if not target_id:
            raise ValueError("target_id is required")
        action = (action or "highlight").strip().lower()
        if action not in SUPPORTED_TRIGGER_ACTIONS:
            raise ValueError(f"unsupported trigger action: {action!r}")
        if cooldown_ms is None:
            cooldown_ms = DEFAULT_COOLDOWN_MS

        triggers = self._triggers.setdefault(lesson_id, {})
        if len(triggers) >= self._max_triggers and trigger_id not in triggers:
            evict = self._pick_eviction(lesson_id)
            if evict is not None:
                del triggers[evict]
            else:
                raise ValueError(
                    f"maximum triggers per lesson reached ({self._max_triggers})"
                )

        tid = trigger_id or f"trg_{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        triggers[tid] = TranscriptTrigger(
            trigger_id=tid,
            lesson_id=lesson_id,
            phrase=phrase,
            target_id=target_id,
            action=action,
            cooldown_ms=int(cooldown_ms),
            enabled=True,
            created_at=now,
            max_fires=max_fires,
            payload=dict(payload or {}),
        )
        logger.info(
            "trigger registered lesson=%s trigger=%s phrase=%r target=%s action=%s",
            lesson_id,
            tid,
            phrase,
            target_id,
            action,
        )
        return tid

    def unregister_trigger(self, lesson_id: str, trigger_id: str) -> bool:
        """Remove one trigger. Returns True if a trigger was removed."""
        triggers = self._triggers.get(lesson_id)
        if not triggers:
            return False
        return triggers.pop(trigger_id, None) is not None

    def clear_triggers(self, lesson_id: str) -> int:
        """Remove all triggers for a lesson (session end / cleanup).

        Returns the number of triggers removed.
        """
        triggers = self._triggers.pop(lesson_id, None)
        self._partial_buffers.pop(lesson_id, None)
        self._partial_last_fired.pop(lesson_id, None)
        count = len(triggers) if triggers else 0
        logger.info("trigger cleanup lesson=%s removed=%d", lesson_id, count)
        return count

    def enable_trigger(self, lesson_id: str, trigger_id: str, enabled: bool = True) -> bool:
        """Enable or disable a trigger without removing it."""
        trigger = self._triggers.get(lesson_id, {}).get(trigger_id)
        if trigger is None:
            return False
        trigger.enabled = bool(enabled)
        return True

    def get_triggers(self, lesson_id: str) -> list[dict[str, Any]]:
        """Return all trigger dicts for a lesson."""
        return [t.to_dict() for t in self._triggers.get(lesson_id, {}).values()]

    def trigger_count(self, lesson_id: str) -> int:
        return len(self._triggers.get(lesson_id, {}))

    def _pick_eviction(self, lesson_id: str) -> str | None:
        """Choose a trigger to evict when at capacity."""
        triggers = self._triggers.get(lesson_id, {})
        if not triggers:
            return None
        # Evict disabled or maxed-out triggers first, then oldest.
        candidates = [
            (tid, t)
            for tid, t in triggers.items()
            if not t.enabled or (t.max_fires is not None and t.fired_count >= t.max_fires)
        ]
        if candidates:
            candidates.sort(key=lambda item: item[1].created_at)
            return candidates[0][0]
        all_triggers = sorted(triggers.values(), key=lambda t: t.created_at)
        return all_triggers[0].trigger_id if all_triggers else None

    # -- transcript handling ------------------------------------------------
    async def handle_transcript(
        self,
        db: Any,
        lesson_id: str,
        text: str,
        *,
        speaker: str = Speaker.TUTOR,
        is_final: bool = True,
    ) -> list[dict[str, Any]]:
        """Process one transcript entry and fire matching triggers.

        Parameters
        ----------
        db:
            Async DB session (used to execute board events via BoardService).
        lesson_id:
            The lesson / session id.
        text:
            Transcript text (partial or final).
        speaker:
            Only ``Speaker.TUTOR`` text drives visual emphasis.
        is_final:
            Whether this is a confirmed final transcript (vs. a streaming
            partial that may be revised).

        Returns a list of execution result dicts, one per fired trigger.
        """
        if not text or not text.strip():
            return []
        if speaker != Speaker.TUTOR:
            # Only the tutor's speech drives board emphasis.
            return []

        text = text.strip()
        now_ms = _now_ms()
        results: list[dict[str, Any]] = []

        if is_final:
            # Flush any partial buffer for this lesson.
            self._partial_buffers.pop(lesson_id, None)
            results.extend(await self._fire_matches(db, lesson_id, text, now_ms))
        else:
            # Partial transcript: accumulate and debounce.
            buffer = self._partial_buffers.get(lesson_id, "")
            combined = (buffer + " " + text).strip() if buffer else text
            self._partial_buffers[lesson_id] = combined
            results.extend(await self._fire_matches(db, lesson_id, combined, now_ms, partial=True))

        return results

    async def _fire_matches(
        self,
        db: Any,
        lesson_id: str,
        text: str,
        now_ms: float,
        *,
        partial: bool = False,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        triggers = list(self._triggers.get(lesson_id, {}).values())
        if not triggers:
            return results

        text_words = normalize_to_words(text)

        for trigger in triggers:
            if not trigger.matches(text):
                continue

            # Partials are debounced more aggressively to avoid flicker.
            if partial:
                last_partial = (
                    self._partial_last_fired.setdefault(lesson_id, {}).get(
                        trigger.trigger_id, 0.0
                    )
                )
                if now_ms - last_partial < PARTIAL_DEBOUNCE_MS:
                    continue

            if not trigger.can_fire(now_ms):
                continue

            result = await self._execute_trigger(db, lesson_id, trigger, now_ms)
            if result.get("ok"):
                trigger.fired_count += 1
                trigger.last_fired_at = _now_iso()
                if partial:
                    self._partial_last_fired.setdefault(lesson_id, {})[
                        trigger.trigger_id
                    ] = now_ms
            results.append(
                {
                    "trigger_id": trigger.trigger_id,
                    "phrase": trigger.phrase,
                    "target_id": trigger.target_id,
                    "action": trigger.action,
                    "result": result,
                }
            )

        return results

    async def _execute_trigger(
        self,
        db: Any,
        lesson_id: str,
        trigger: TranscriptTrigger,
        now_ms: float,  # noqa: ARG002 - reserved for future use (telemetry)
    ) -> dict[str, Any]:
        """Execute one fired trigger as an existing board_highlight event."""
        style = _ACTION_TO_STYLE.get(trigger.action, "box")
        label: str | None = None
        if trigger.action == "annotation":
            label = f"[{trigger.phrase}]"
        args: dict[str, Any] = {
            "target_ids": trigger.payload.get("target_ids") or [trigger.target_id],
            "style": style,
            "color": "yellow" if trigger.action != "reveal" else None,
            "label": label,
        }
        for key in ("stage", "plan_element_id", "duration_ms"):
            if trigger.payload.get(key) is not None:
                args[key] = trigger.payload[key]

        try:
            result = await self._registry.execute_structured(
                db,
                lesson_id,
                "board_highlight",
                args,
                source="system",
                animate=True,
            )
        except BoardValidationError as exc:
            logger.warning(
                "trigger execution rejected lesson=%s trigger=%s error=%s",
                lesson_id,
                trigger.trigger_id,
                exc.message,
            )
            return {"ok": False, "error": exc.code, "message": exc.message, "retryable": True}
        except Exception:  # pragma: no cover - defensive, must not break audio
            logger.exception(
                "trigger execution failed lesson=%s trigger=%s",
                lesson_id,
                trigger.trigger_id,
            )
            return {
                "ok": False,
                "error": "internal_error",
                "message": "the trigger action could not be completed",
                "retryable": False,
            }

        if result.get("ok"):
            logger.info(
                "trigger fired lesson=%s trigger=%s phrase=%r target=%s action=%s",
                lesson_id,
                trigger.trigger_id,
                trigger.phrase,
                trigger.target_id,
                trigger.action,
            )
        else:
            logger.warning(
                "trigger execution not ok lesson=%s trigger=%s result=%s",
                lesson_id,
                trigger.trigger_id,
                result,
            )
        return result


__all__ = [
    "TranscriptTrigger",
    "TranscriptTriggerSystem",
    "SUPPORTED_TRIGGER_ACTIONS",
    "DEFAULT_COOLDOWN_MS",
    "normalize_to_words",
    "phrase_in_text",
]
