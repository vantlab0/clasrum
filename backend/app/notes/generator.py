"""Structured lesson-note generation.

Two generators share one validated output shape:

* :func:`generate_with_llm` — OpenAI-compatible chat completion (DashScope
  compatible endpoint, ``settings.notes_model``) asked for strict JSON.
* :func:`generate_offline` — deterministic, fully local extraction from the
  transcript + board so the product flow works with no API key (CI, demos).
  It never invents facts: every sentence is derived from the lesson row, the
  persisted plan, transcript turns, board elements or assessment rows.

Any failure raises :class:`NotesGenerationError`; the service persists the note
as ``failed`` with the message while leaving the transcript untouched.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.contracts import dtos
from app.db.models import Lesson, LessonAssessment, LessonTranscriptEntry
from app.notes.board_context import element_heading, element_text

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 300
MAX_SECTIONS = 30
MAX_SECTION_HEADING_CHARS = 160
MAX_SECTION_BODY_CHARS = 8000
MAX_TAKEAWAYS = 10
MAX_QUESTIONS = 10
MAX_BOARD_REFERENCES = 60
MAX_TRANSCRIPT_LINE_CHARS = 4000

_PLACEHOLDER_SECTION_HEADINGS = {"key takeaways", "revision questions"}


class NotesGenerationError(RuntimeError):
    """Raised when structured notes cannot be produced or validated."""


@dataclass
class NoteContext:
    """Everything the generators are allowed to read."""

    lesson: Lesson
    plan: dtos.LessonPlan
    transcript: list[LessonTranscriptEntry]
    board_elements: list[dict[str, Any]]
    assessments: list[LessonAssessment] = field(default_factory=list)


@dataclass
class NotesResult:
    """Validated generator output, ready to persist on ``lesson_notes``."""

    title: str
    sections: list[dict[str, Any]]
    key_takeaways: list[str]
    revision_questions: list[str]
    board_references: list[dict[str, Any]]
    content_markdown: str
    generated_by: str

    def note_title(self) -> str:
        return self.title

    def note_sections(self) -> list[dict[str, Any]]:
        return self.sections

    def board_reference_json(self) -> dict[str, Any]:
        return {
            "elements": self.board_references,
            "key_takeaways": self.key_takeaways,
            "revision_questions": self.revision_questions,
        }


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
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item)


def _transcript_for_prompt(entries: list[LessonTranscriptEntry], max_chars: int) -> str:
    lines = []
    for entry in entries:
        text = (entry.text or "").strip()[:MAX_TRANSCRIPT_LINE_CHARS]
        if not text:
            continue
        lines.append(f"[{entry.speaker}] {text}")
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    head_len = max(1, max_chars * 2 // 3)
    tail_len = max(1, max_chars - head_len)
    return joined[:head_len] + "\n... [transcript truncated] ...\n" + joined[-tail_len:]


def _board_for_prompt(elements: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for element in elements:
        text = element_text(element)
        if not text:
            continue
        label = text[:500]
        lines.append(
            f"[{element.get('element_id')}] ({element.get('type')}) {label}"
        )
    return "\n".join(lines)


def render_markdown(
    title: str,
    sections: list[dict[str, Any]],
    *,
    transcript_turns: int,
    board_count: int,
) -> str:
    parts: list[str] = [f"# {title}", ""]
    for section in sections:
        parts.append(f"## {section['heading']}")
        parts.append("")
        body = str(section.get("body") or "").strip()
        if body:
            parts.append(body)
            parts.append("")
        latex = section.get("latex")
        if latex:
            parts.append("```latex")
            parts.append(str(latex))
            parts.append("```")
            parts.append("")
    parts.append("---")
    parts.append(
        f"_Generated from {transcript_turns} transcript turn(s) and "
        f"{board_count} board element(s)._"
    )
    return "\n".join(parts).strip() + "\n"


# ---------------------------------------------------------------------------
# offline deterministic generator
# ---------------------------------------------------------------------------


def _plan_sections(plan: dtos.LessonPlan) -> tuple[list[dict[str, Any]], list[str]]:
    sections: list[dict[str, Any]] = []
    takeaways: list[str] = []
    if plan.objectives:
        body = "The lesson set out to:\n" + "\n".join(
            f"- {objective}" for objective in plan.objectives
        )
        sections.append({"heading": "Learning Objectives", "body": body, "latex": None})
        takeaways.extend(plan.objectives)
    if plan.concepts:
        lines = [
            f"- {concept.title} ({concept.status.replace('_', ' ')})"
            for concept in plan.concepts
        ]
        sections.append(
            {
                "heading": "Concept Map",
                "body": "Concepts covered in this lesson, in teaching order:\n"
                + "\n".join(lines),
                "latex": None,
            }
        )
        takeaways.extend(concept.title for concept in plan.concepts)
    return sections, takeaways


def _board_sections(
    elements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    sections: list[dict[str, Any]] = []
    references: list[dict[str, str]] = []
    takeaways: list[str] = []
    current: dict[str, Any] | None = None
    default_section: dict[str, Any] | None = None

    def ensure_section() -> dict[str, Any]:
        nonlocal current, default_section
        if current is not None:
            return current
        if default_section is None:
            default_section = {
                "heading": "Board Notes",
                "body_lines": [],
                "latex": None,
            }
            sections.append(default_section)
        return default_section

    for element in elements:
        element_type = str(element.get("type") or "")
        payload = element.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        heading = element_heading(element)
        text = element_text(element)
        if heading:
            current = {"heading": heading, "body_lines": [], "latex": None}
            sections.append(current)
            references.append(
                {
                    "element_id": str(element.get("element_id") or ""),
                    "type": element_type,
                    "label": heading[:120],
                }
            )
            takeaways.append(heading)
            continue
        if not text:
            continue
        section = ensure_section()
        if element_type == "write_definition":
            term = _clean_text(payload.get("term"), 120)
            definition = _clean_text(payload.get("text"), 1000)
            line = f"- **{term}**: {definition}" if term else f"- {definition}"
            section["body_lines"].append(line)
            if term:
                takeaways.append(f"{term}: {definition}"[:240])
        elif element_type == "write_bullet_list":
            items = payload.get("items") or []
            for item in items:
                item_text = _clean_text(item, 500)
                if item_text:
                    section["body_lines"].append(f"- {item_text}")
        elif element_type in {"write_equation", "update_equation"}:
            latex = _clean_text(payload.get("latex"), 2000)
            if latex:
                if not section.get("latex"):
                    section["latex"] = latex
                section["body_lines"].append(f"Equation: {latex}")
        else:
            section["body_lines"].append(f"- {text[:1000]}")
        references.append(
            {
                "element_id": str(element.get("element_id") or ""),
                "type": element_type,
                "label": text[:120],
            }
        )

    for section in sections:
        lines = section.pop("body_lines", [])
        section["body"] = "\n".join(lines) if lines else ""
    return sections, references, takeaways


def _transcript_sections(
    entries: list[LessonTranscriptEntry],
) -> tuple[list[dict[str, Any]], list[str]]:
    sections: list[dict[str, Any]] = []
    takeaways: list[str] = []
    tutor_lines = [
        _clean_text(entry.text, 800)
        for entry in entries
        if entry.speaker == "tutor" and len((entry.text or "").strip()) >= 40
    ]
    tutor_lines = [line for line in tutor_lines if line][:10]
    if tutor_lines:
        sections.append(
            {
                "heading": "Tutor Explanations",
                "body": "Key explanations from the tutorial:\n" + _bullets(tutor_lines),
                "latex": None,
            }
        )
        takeaways.extend(line[:180] for line in tutor_lines[:4])
    student_lines = [
        _clean_text(entry.text, 500)
        for entry in entries
        if entry.speaker == "student"
    ]
    student_lines = [line for line in student_lines if line][:8]
    if student_lines:
        sections.append(
            {
                "heading": "Student Contributions",
                "body": "What the student asked or answered during the session:\n"
                + _bullets(student_lines),
                "latex": None,
            }
        )
    return sections, takeaways


def _assessment_questions(assessments: list[LessonAssessment]) -> list[str]:
    questions = [
        _clean_text(assessment.question, 400)
        for assessment in assessments
        if (assessment.question or "").strip()
    ]
    return _dedupe([question for question in questions if question])


def _revision_questions(
    context: NoteContext, board_elements: list[dict[str, Any]]
) -> list[str]:
    questions = _assessment_questions(context.assessments)
    if questions:
        return questions[:MAX_QUESTIONS]
    for element in board_elements:
        if element.get("type") == "write_definition":
            term = _clean_text((element.get("payload") or {}).get("term"), 120)
            if term:
                questions.append(f"Explain \"{term}\" in your own words.")
    for concept in context.plan.concepts:
        questions.append(f"Explain the concept \"{concept.title}\".")
    if not questions:
        for element in board_elements:
            heading = element_heading(element)
            if heading:
                questions.append(f"Summarize the key points about \"{heading}\".")
    return _dedupe(questions)[:MAX_QUESTIONS]


def generate_offline(context: NoteContext) -> NotesResult:
    """Build structured notes strictly from persisted lesson content."""
    lesson = context.lesson
    transcript = context.transcript
    board_elements = context.board_elements
    tutor_turns = sum(1 for entry in transcript if entry.speaker == "tutor")
    student_turns = sum(1 for entry in transcript if entry.speaker == "student")

    sections: list[dict[str, Any]] = []
    overview_lines = [f"Topic: {lesson.topic}."]
    if lesson.subject:
        overview_lines.append(f"Subject: {lesson.subject}.")
    overview_lines.append(
        f"Level: {str(lesson.learning_level).replace('_', ' ')}. "
        f"Planned duration: {lesson.duration_minutes} minutes."
    )
    if transcript:
        overview_lines.append(
            f"The recorded session contains {len(transcript)} transcript turn(s) "
            f"({tutor_turns} tutor, {student_turns} student) and "
            f"{len(board_elements)} board element(s)."
        )
    else:
        overview_lines.append(
            "No transcript turns were recorded for this lesson, so these notes "
            "cover only the lesson plan and board content."
        )
    sections.append(
        {
            "heading": "Overview",
            "body": " ".join(overview_lines),
            "latex": None,
        }
    )

    plan_sections, plan_takeaways = _plan_sections(context.plan)
    sections.extend(plan_sections)

    board_sections, board_refs, board_takeaways = _board_sections(board_elements)
    sections.extend(board_sections)

    transcript_sections, transcript_takeaways = _transcript_sections(transcript)
    sections.extend(transcript_sections)

    takeaways = _dedupe(plan_takeaways + board_takeaways + transcript_takeaways)[
        :MAX_TAKEAWAYS
    ]
    if not takeaways:
        takeaways = [f"Reviewed the topic: {lesson.topic}."]
    sections.append(
        {
            "heading": "Key Takeaways",
            "body": _bullets(takeaways),
            "latex": None,
        }
    )

    questions = _revision_questions(context, board_elements)
    if not questions:
        questions = [f"What did you learn about {lesson.topic}?"]
    sections.append(
        {
            "heading": "Revision Questions",
            "body": _bullets(questions),
            "latex": None,
        }
    )

    title = f"{_clean_text(lesson.topic, 200)} — Lesson Notes"
    markdown = render_markdown(
        title,
        sections,
        transcript_turns=len(transcript),
        board_count=len(board_elements),
    )
    return NotesResult(
        title=title,
        sections=sections,
        key_takeaways=takeaways,
        revision_questions=questions,
        board_references=board_refs[:MAX_BOARD_REFERENCES],
        content_markdown=markdown,
        generated_by="offline:deterministic",
    )


# ---------------------------------------------------------------------------
# LLM (OpenAI-compatible) generator
# ---------------------------------------------------------------------------

_NOTES_SYSTEM_PROMPT = (
    "You are an expert tutor writing structured lesson notes for a student. "
    "Use ONLY the lesson plan, transcript and board content supplied by the "
    "user. Never invent facts, definitions, dates or examples that are not "
    "grounded in that material. Write clear study notes in the requested JSON "
    "schema. Return JSON only, with no markdown fences and no commentary."
)

_NOTES_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "sections",
        "key_takeaways",
        "revision_questions",
        "board_references",
    ],
    "properties": {
        "title": {"type": "string"},
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "body"],
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                    "latex": {"type": ["string", "null"]},
                },
            },
        },
        "key_takeaways": {
            "type": "array",
            "items": {"type": "string"},
        },
        "revision_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "board_references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["element_id"],
                "properties": {
                    "element_id": {"type": "string"},
                    "label": {"type": "string"},
                },
            },
        },
    },
}


def _build_user_prompt(context: NoteContext, settings: Settings) -> str:
    lesson = context.lesson
    plan_lines = [
        f"- {concept.title} (status: {concept.status})"
        for concept in context.plan.concepts
    ]
    objectives = "\n".join(f"- {objective}" for objective in context.plan.objectives)
    transcript_text = _transcript_for_prompt(
        context.transcript, settings.notes_max_transcript_chars
    )
    board_text = _board_for_prompt(context.board_elements)
    assessment_lines = []
    for assessment in context.assessments:
        line = f"- Q: {_clean_text(assessment.question, 500)}"
        if assessment.student_answer:
            line += f" | student: {_clean_text(assessment.student_answer, 300)}"
        if assessment.correct is not None:
            line += " | correct" if assessment.correct else " | incorrect"
        assessment_lines.append(line)
    return (
        "Create structured study notes for this finished tutorial.\n\n"
        f"Topic: {lesson.topic}\n"
        f"Subject: {lesson.subject or 'unspecified'}\n"
        f"Level: {lesson.learning_level}\n"
        f"Planned duration: {lesson.duration_minutes} minutes\n\n"
        f"Learning objectives:\n{objectives or '(none)'}\n\n"
        f"Planned concepts (in order):\n{chr(10).join(plan_lines) or '(none)'}\n\n"
        f"Board content (element_id | type | text):\n{board_text or '(empty board)'}\n\n"
        f"Assessment records:\n{chr(10).join(assessment_lines) or '(none)'}\n\n"
        f"Transcript:\n{transcript_text or '(no transcript)'}\n\n"
        "Return JSON with: a short title; 3-8 sections (heading + markdown body, "
        "optional latex for a single equation); 3-6 key takeaways; 3-6 revision "
        "questions; and board_references containing the element_id values your "
        "notes cite. Only cite element ids that appear in the board content."
    )


def _parse_json_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise NotesGenerationError("notes model did not return a JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise NotesGenerationError(f"notes model returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise NotesGenerationError("notes model returned non-object JSON")
    return payload


def _normalise_payload(
    payload: dict[str, Any],
    context: NoteContext,
    *,
    generated_by: str,
) -> NotesResult:
    """Validate + clamp untrusted model output into the contract shape."""
    title = _clean_text(payload.get("title"), MAX_TITLE_CHARS)
    if not title:
        title = f"{_clean_text(context.lesson.topic, 200)} — Lesson Notes"

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise NotesGenerationError("notes model returned no sections")
    sections: list[dict[str, Any]] = []
    for raw in raw_sections[:MAX_SECTIONS]:
        if not isinstance(raw, dict):
            raise NotesGenerationError("notes model returned a malformed section")
        heading = _clean_text(raw.get("heading"), MAX_SECTION_HEADING_CHARS)
        body = str(raw.get("body") or "").strip()[:MAX_SECTION_BODY_CHARS]
        if not heading or not body:
            raise NotesGenerationError(
                "notes model returned a section with an empty heading or body"
            )
        if heading.lower() in _PLACEHOLDER_SECTION_HEADINGS:
            continue
        latex = raw.get("latex")
        if latex is not None and not isinstance(latex, str):
            latex = None
        sections.append(
            {"heading": heading, "body": body, "latex": latex or None}
        )
    if not sections:
        raise NotesGenerationError("notes model returned no usable sections")

    takeaways = _dedupe(
        [
            _clean_text(item, 300)
            for item in (payload.get("key_takeaways") or [])
            if isinstance(item, str)
        ]
    )[:MAX_TAKEAWAYS]
    questions = _dedupe(
        [
            _clean_text(item, 400)
            for item in (payload.get("revision_questions") or [])
            if isinstance(item, str)
        ]
    )[:MAX_QUESTIONS]
    if not takeaways:
        raise NotesGenerationError("notes model returned no key takeaways")
    if not questions:
        raise NotesGenerationError("notes model returned no revision questions")

    known_ids = {
        str(element.get("element_id"))
        for element in context.board_elements
        if element.get("element_id")
    }
    references: list[dict[str, str]] = []
    raw_refs = payload.get("board_references") or []
    if isinstance(raw_refs, list):
        for raw in raw_refs[:MAX_BOARD_REFERENCES]:
            if not isinstance(raw, dict):
                continue
            element_id = str(raw.get("element_id") or "")
            if element_id not in known_ids:
                continue
            references.append(
                {
                    "element_id": element_id,
                    "type": "",
                    "label": _clean_text(raw.get("label"), 200),
                }
            )

    sections.append(
        {
            "heading": "Key Takeaways",
            "body": _bullets(takeaways),
            "latex": None,
        }
    )
    sections.append(
        {
            "heading": "Revision Questions",
            "body": _bullets(questions),
            "latex": None,
        }
    )
    markdown = render_markdown(
        title,
        sections,
        transcript_turns=len(context.transcript),
        board_count=len(context.board_elements),
    )
    return NotesResult(
        title=title,
        sections=sections,
        key_takeaways=takeaways,
        revision_questions=questions,
        board_references=references,
        content_markdown=markdown,
        generated_by=generated_by,
    )


async def generate_with_llm(
    context: NoteContext, settings: Settings, api_key: str | None = None
) -> NotesResult:
    """Call an OpenAI-compatible chat endpoint and validate the JSON output."""
    key = api_key or settings.effective_notes_api_key
    if not key:
        raise NotesGenerationError("no notes API key configured")
    url = settings.notes_base_url.rstrip("/") + "/chat/completions"
    request_body = {
        "model": settings.notes_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _NOTES_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(context, settings)},
        ],
        "response_format": {"type": "json_object"},
        "json_schema": _NOTES_JSON_SCHEMA,
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
        raise NotesGenerationError(
            f"notes model returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise NotesGenerationError(f"notes model request failed: {exc}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise NotesGenerationError(
            f"notes model response had an unexpected shape: {exc}"
        ) from exc
    payload = _parse_json_content(content)
    return _normalise_payload(
        payload, context, generated_by=f"openai:{settings.notes_model}"
    )


async def generate(context: NoteContext, settings: Settings) -> NotesResult:
    """Run the configured generator: LLM when a key exists, else offline."""
    if settings.effective_notes_api_key:
        return await generate_with_llm(context, settings)
    return generate_offline(context)


__all__ = [
    "NoteContext",
    "NotesGenerationError",
    "NotesResult",
    "generate",
    "generate_offline",
    "generate_with_llm",
    "render_markdown",
]
