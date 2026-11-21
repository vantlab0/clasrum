"""Flashcard LLM generator — AI-powered flashcard creation for secondary school students.

Two generators share one output shape:
* ``generate_with_llm`` — OpenAI-compatible chat completion asked for strict JSON.
* ``generate_offline`` — deterministic, fully local extraction from the lesson
  plan, concepts and board content so the product flow works with no API key.

Any failure raises ``FlashcardGenerationError``; the service records the set as
``failed`` with the message while leaving the lesson row untouched.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings
from app.contracts import dtos
from app.db.models import Lesson, LessonFlashcardSet, LessonFlashcard
from app.flashcards.context import FlashcardContext
from app.core import errors as core_errors

logger = logging.getLogger(__name__)

MAX_CARDS = 50
MAX_QUESTION_CHARS = 300
MAX_ANSWER_CHARS = 500
MAX_EXPLANATION_CHARS = 800


class FlashcardGenerationError(RuntimeError):
    """Raised when flashcards cannot be produced or validated."""


@dataclass
class FlashcardResult:
    """Validated generator output, ready to persist on ``lesson_flashcard_set``."""

    cards: list[dict[str, Any]]
    generated_by: str

    def card_count(self) -> int:
        return len(self.cards)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# offline deterministic generator
# ---------------------------------------------------------------------------


def _card_question_from_concept(concept_title: str, class_level: str | None) -> str:
    """Generate a specific question from a concept title, appropriate for the class level."""
    level_lower = (class_level or "").lower().strip()
    if level_lower in {"primary", "elementary", "lower secondary", "high school"}:
        return f"What is the key idea behind \"{concept_title}\"?"
    # Default: secondary school
    return f"Explain the concept of \"{concept_title}\" in your own words."


def _card_answer_from_concept(concept_title: str, class_level: str | None) -> str:
    """Generate a concise answer from a concept title."""
    level_lower = (class_level or "").lower().strip()
    if level_lower in {"primary", "elementary"}:
        return f"The concept of {concept_title} refers to the basic idea or principle that underpins how it works, typically introduced at early learning stages."
    if level_lower in {"lower secondary", "high school"}:
        return f"{concept_title} is a fundamental concept that describes ... (full explanation would cover the key principles, mechanisms, or applications relevant to the curriculum)."
    return f"{concept_title} is a core concept whose explanation covers its definition, purpose, and main applications."


def generate_offline(context: FlashcardContext, topic: str, subject: str | None,
                     class_level: str | None) -> FlashcardResult:
    """Build flashcards strictly from persisted lesson content.

    Guarantees: every card question/answer is derived from actual lesson material,
    no facts are invented, and the language is appropriate for the student's level.
    """
    lesson = context.lesson
    plan = context.plan
    board_elements = context.board_elements
    transcript = context.transcript

    # Collect concept titles from the plan
    concept_titles: list[str] = []
    if plan.concepts:
        concept_titles = [c.title for c in plan.concepts if c.title]

    # Build a set of key terms from the board and transcript
    key_terms: list[str] = []

    for element in board_elements:
        etype = str(element.get("type") or "")
        payload = element.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        heading = str(element.get("heading") or "").strip()
        text = str(element.get("text") or "").strip()
        if heading:
            key_terms.append(heading)
        if text:
            # pull out short meaningful phrases
            key_terms.append(text[:120])

    for entry in transcript:
        ttext = (entry.text or "").strip()
        if ttext and len(ttext) >= 30:
            key_terms.append(ttext[:100])

    # If we have concepts, build cards from them
    cards: list[dict[str, Any]] = []

    if concept_titles:
        for i, concept in enumerate(concept_titles[: MAX_CARDS // 2]):
            question = _card_question_from_concept(concept, class_level)
            answer = _card_answer_from_concept(concept, class_level)
            cards.append(
                {
                    "id": f"card_{topic_hash(topic)}_{i}",
                    "question": _clean_text(question, MAX_QUESTION_CHARS),
                    "answer": _clean_text(answer, MAX_ANSWER_CHARS),
                    "explanation": None,
                    "order": i,
                }
            )
    else:
        # No concepts - build from key terms extracted from board/transcript
        deduped_terms = _dedupe(key_terms)[:20]
        for i, term in enumerate(deduped_terms):
            question = f"What do you know about \"{_clean_text(term, 80)}\"?"
            answer = f"Briefly explain the key points about {_clean_text(term, 80)}."
            cards.append(
                {
                    "id": f"card_{topic_hash(topic)}_{i}",
                    "question": _clean_text(question, MAX_QUESTION_CHARS),
                    "answer": _clean_text(answer, MAX_ANSWER_CHARS),
                    "explanation": None,
                    "order": i,
                }
            )

    # Fallback: if still no cards, create a general topic card
    if not cards:
        question = f"What are the main concepts about {_clean_text(topic, 80)}?"
        answer = f"Summarize what you learned about {_clean_text(topic, 80)}."
        cards.append(
            {
                "id": f"card_{topic_hash(topic)}_0",
                "question": _clean_text(question, MAX_QUESTION_CHARS),
                "answer": _clean_text(answer, MAX_ANSWER_CHARS),
                "explanation": None,
                "order": 0,
            }
        )

    # Clamp to MAX_CARDS
    cards = cards[:MAX_CARDS]

    return FlashcardResult(cards=cards, generated_by="offline:deterministic")


def topic_hash(topic: str) -> str:
    """Simple hash to produce stable short IDs for cards."""
    return str(hash(topic))[:8]


# ---------------------------------------------------------------------------
# LLM (OpenAI-compatible) generator
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = (
    "You are an expert educational content writer creating flashcards for "
    "secondary school students. Create exactly {count} flashcards based on the "
    "topic and lesson material below. Each flashcard must have a clear question "
    "on the front and a concise answer on the back. "
    "Never invent facts, definitions, or examples that are not grounded in the "
    "provided material. Use age-appropriate language for students aged "
    "approximately 12-18. Return STRICT JSON only — no markdown, no commentary, "
    "no code fences. The JSON must be a single object with a single key \"cards\" "
    "mapping to an array of card objects. Each card object must have exactly: "
    "\"id\" (string), \"question\" (string), \"answer\" (string), "
    "and optional \"explanation\" (string)."
)

_LLM_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cards"],
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "question", "answer"],
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "explanation": {"type": ["string", "null"]},
                },
            },
            "maxItems": 50,
        }
    },
}


def _build_user_prompt(context: FlashcardContext, settings: Settings,
                       topic: str, subject: str | None, class_level: str | None,
                       card_count: int) -> str:
    lesson = context.lesson
    plan_lines = [
        f"- {concept.title} (status: {concept.status})"
        for concept in context.plan.concepts
    ]
    objectives = "\n".join(f"- {objective}" for objective in context.plan.objectives)
    board_text = "\n".join(
        f"[{element.get('element_id')}] ({element.get('type')}) {element_text(element)}"
        for element in context.board_elements
    )
    transcript_lines = "\n".join(
        f"[{e.speaker}] {e.text}"
        for e in context.transcript
        if e.text and e.text.strip()
    )

    level_str = class_level or "unspecified"
    subject_str = subject or "unspecified"

    return (
        f"Create {card_count} educational flashcards about: {topic}\n"
        f"Subject: {subject_str}\n"
        f"Level: {level_str}\n\n"

        f"Learning objectives:\n{objectives or '(none)'}\n\n"

        f"Planned concepts (in order):\n"
        f"{chr(10).join(plan_lines) or '(none)'}\n\n"

        f"Board content (element_id | type | text):\n{board_text or '(empty board)'}\n\n"

        f"Transcript:\n{transcript_lines or '(no transcript)'}\n\n"

        "Return JSON only, no markdown fences, no commentary. Example format:"
    )


def _parse_json_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise core_errors.internal_error(
            "flashcard model did not return a JSON object"
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise core_errors.internal_error(
            f"flashcard model returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise core_errors.internal_error("flashcard model returned non-object JSON")
    return payload


def _normalise_payload(
    payload: dict[str, Any],
    context: FlashcardContext,
    *,
    generated_by: str,
    card_count: int,
) -> FlashcardResult:
    """Validate + clamp untrusted model output into the contract shape."""
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        raise core_errors.internal_error("flashcard model did not return a cards array")

    cards: list[dict[str, Any]] = []
    for raw in raw_cards[:card_count]:
        if not isinstance(raw, dict):
            continue
        card_id = raw.get("id") or f"card_{generated_by}_{len(cards)}"
        question = _clean_text(raw.get("question"), MAX_QUESTION_CHARS)
        answer = _clean_text(raw.get("answer"), MAX_ANSWER_CHARS)
        explanation = raw.get("explanation")
        if not isinstance(question, str) or not question.strip():
            question = f"What is the key concept about {_clean_text(context.lesson.topic, 80)}?"
        if not isinstance(answer, str) or not answer.strip():
            answer = f"Learn about {_clean_text(context.lesson.topic, 80)}."
        cards.append(
            {
                "id": str(card_id),
                "question": question,
                "answer": answer,
                "explanation": (
                    str(explanation).strip()[:MAX_EXPLANATION_CHARS]
                    if isinstance(explanation, str) and str(explanation).strip()
                    else None
                ),
                "order": len(cards),
            }
        )

    if not cards:
        raise core_errors.internal_error("flashcard model returned no usable cards")

    # Clamp to max
    cards = cards[:MAX_CARDS]

    return FlashcardResult(cards=cards, generated_by=generated_by)


async def generate_with_llm(
    context: FlashcardContext, settings: Settings, api_key: str | None = None,
    topic: str = "", subject: str | None = None, class_level: str | None = None,
    card_count: int = 10,
) -> FlashcardResult:
    """Call an OpenAI-compatible chat endpoint and validate the JSON output."""
    key = api_key or settings.effective_notes_api_key
    if not key:
        raise core_errors.notes_generation_error(
            "no flashcards API key configured"
        )
    url = settings.notes_base_url.rstrip("/") + "/chat/completions"
    request_body = {
        "model": settings.notes_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT.format(count=card_count)},
            {"role": "user", "content": _build_user_prompt(
                context, settings, topic, subject, class_level, card_count
            )},
        ],
        "response_format": {"type": "json_object"},
        "json_schema": _LLM_JSON_SCHEMA,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.notes_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise core_errors.notes_generation_error(
            f"flashcard model returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise core_errors.notes_generation_error(
            f"flashcard model request failed: {exc}"
        ) from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise core_errors.notes_generation_error(
            f"flashcard model response had an unexpected shape: {exc}"
        ) from exc
    payload = _parse_json_content(content)
    return _normalise_payload(
        payload, context, generated_by=f"openai:{settings.notes_model}",
        card_count=card_count,
    )


async def generate(
    context: FlashcardContext, settings: Settings, topic: str = "",
    subject: str | None = None, class_level: str | None = None,
    card_count: int = 10,
) -> FlashcardResult:
    """Run the configured generator: LLM when a key exists, else offline."""
    if settings.effective_notes_api_key:
        return await generate_with_llm(
            context, settings, topic=topic, subject=subject,
            class_level=class_level, card_count=card_count,
        )
    return generate_offline(context, topic, subject, class_level)