"""Qwen Realtime transport over the real DashScope WebSocket.

Authority: ``docs/CONTRACTS.md`` §6 ("Qwen Realtime wire facts").

The adapter frames messages to DashScope's realtime API and consumes server
events. There is no offline/fake transport: lessons require a valid API key.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)

REALTIME_URL = get_settings().qwen_realtime_url
MODEL = get_settings().qwen_model
VOICE = get_settings().qwen_voice
INPUT_RATE = get_settings().qwen_input_sample_rate
OUTPUT_RATE = get_settings().qwen_output_sample_rate

# ---------------------------------------------------------------------------
# real DashScope transport
# ---------------------------------------------------------------------------


class QwenRealtimeSession:
    """Real Qwen Realtime session over DashScope WebSocket."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._ws: websockets.ClientConnection | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self, instructions: str, tools: list[dict[str, Any]]) -> None:
        settings = get_settings()
        url = f"{REALTIME_URL}?model={MODEL}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-DataInspection": "disable",
        }
        handshake_timeout = max(1.0, float(getattr(settings, "qwen_handshake_timeout_seconds", 20.0)))
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(url, additional_headers=headers),
                timeout=handshake_timeout,
            )
        except Exception:
            await self.aclose()
            raise

        self._reader_task = asyncio.create_task(self._read_loop())

        # semantic_vad / semantic_vad_ec detect end-of-speech by meaning, so
        # backchannels and background noise do not trigger a turn, while real
        # user speech can interrupt the tutor. The acoustic threshold is only
        # meaningful for server_vad.
        turn_detection: dict[str, Any] = {"type": settings.qwen_turn_detection}
        if str(settings.qwen_turn_detection).startswith("server_vad"):
            turn_detection["threshold"] = 0.5
        turn_detection["silence_duration_ms"] = int(settings.qwen_vad_silence_ms)
        try:
            await self._send(
                {
                    "type": "session.update",
                    "session": {
                        "instructions": instructions,
                        "modalities": ["audio", "text"],
                        "voice": VOICE,
                        "input_audio_format": "pcm",
                        "output_audio_format": "pcm",
                        "turn_detection": turn_detection,
                        "input_audio_transcription": {
                            "model": "qwen3-asr-flash-realtime",
                        },
                        "tools": tools,
                    },
                }
            )
            # Wait for session.updated (readiness) with a timeout.
            deadline = asyncio.get_event_loop().time() + handshake_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Qwen session.update readiness timed out")
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise TimeoutError("Qwen session.update readiness timed out")
                if event is None:
                    raise ConnectionError("Qwen WebSocket closed before session.updated")
                etype = event.get("type")
                if etype == "session.updated":
                    return
                if etype == "error":
                    err = event.get("error") if isinstance(event.get("error"), dict) else {}
                    msg = err.get("message") or event.get("message") or "Qwen realtime error"
                    code = err.get("code") or event.get("code")
                    detail = f"{code}: {msg}" if code else str(msg)
                    raise ConnectionError(f"Qwen error during handshake: {detail}")
                # Ignore other events during handshake (e.g. session.created).
        except Exception:
            await self.aclose()
            raise

    async def append_audio(self, payload_b64: str) -> None:
        await self._send(
            {"type": "input_audio_buffer.append", "audio": payload_b64}
        )

    async def commit_audio(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def create_response(self) -> None:
        await self._send({"type": "response.create"})

    async def cancel_response(self) -> None:
        await self._send({"type": "response.cancel"})

    async def function_call_output(
        self, call_id: str, output: str, *, continue_response: bool = True
    ) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )
        # Most tools need a model continuation. Flow-control tools such as
        # wait_for_student deliberately do not: the tutor must stop and listen.
        if continue_response:
            await self.create_response()

    async def prompt_text(self, text: str) -> None:
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self.create_response()

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def aclose(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._ws is not None:
            await self._ws.close()

    # -- internals -------------------------------------------------------

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Qwen session not connected")
        await self._ws.send(json.dumps(payload))

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                event = json.loads(message)
                await self._queue.put(event)
        except Exception as exc:  # pragma: no cover - transport errors
            logger.warning("Qwen read loop ended: %s", exc)
        finally:
            await self._queue.put(None)


__all__ = ["QwenRealtimeSession"]
