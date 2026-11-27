"""Regression tests: realtime board writes are one short line per tool call."""

from app.board.schemas import (
    MAX_BOARD_LINE_CHARS,
    MAX_BOARD_LINE_WORDS,
    WriteHeadingArgs,
    WriteOnBoardArgs,
)


def _write(text: str, style: str = "body") -> dict:
    return WriteOnBoardArgs(
        text=text,
        style=style,
        position="next_line",
        writing_style="progressive",
    ).build_payload()


def test_short_line_is_untouched() -> None:
    assert _write("Sunlight")["text"] == "Sunlight"
    assert _write("Plants make food")["text"] == "Plants make food"


def test_multiline_is_collapsed_to_one_line() -> None:
    payload = _write("Photosynthesis\n makes food\n using sunlight")
    assert payload["text"] == "Photosynthesis makes food using sunlight"
    assert "\n" not in payload["text"]


def test_paragraph_cannot_be_dumped_in_one_call() -> None:
    payload = _write(
        "Photosynthesis is the process by which green plants use sunlight to "
        "make food from carbon dioxide and water in the presence of chlorophyll."
    )
    text = payload["text"]
    assert "\n" not in text
    assert len(text.split()) <= MAX_BOARD_LINE_WORDS
    assert len(text) <= MAX_BOARD_LINE_CHARS


def test_title_is_a_single_h2_heading_line() -> None:
    payload = _write("Introduction to Photosynthesis", "h1")
    assert payload["text"] == "Introduction to Photosynthesis"
    assert payload["style"] == "subheading"
    assert payload["heading_level"] == 2
    assert _write("Introduction to Photosynthesis", "h2") == payload


def test_legacy_write_heading_level_one_renders_as_h2() -> None:
    payload = WriteHeadingArgs(
        text="Photosynthesis",
        position={"x": 0.1, "y": 0.1},
        level=1,
    ).build_payload()
    assert payload["level"] == 2
