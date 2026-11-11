"""Structured lesson notes: generation and persistence."""
from app.notes.generator import (
    NoteContext,
    NotesGenerationError,
    NotesResult,
    generate,
    generate_offline,
    generate_with_llm,
)
from app.notes.service import (
    build_context,
    default_note_title,
    request_note_generation,
    run_generation,
)

__all__ = [
    "NoteContext",
    "NotesGenerationError",
    "NotesResult",
    "generate",
    "generate_offline",
    "generate_with_llm",
    "build_context",
    "default_note_title",
    "request_note_generation",
    "run_generation",
]
