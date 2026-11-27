"""Backend/Flutter typography contract parity tests.

The backend reserves board space; Flutter paints the chalk.  If the two drift,
consecutive headings overlap even though the backend believes they fit.  These
tests parse the Dart source so drift fails the backend suite immediately.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.board.layout import (
    DEFAULT_CONFIG,
    TEXT_STYLE_SPECS,
    TextStyleMetrics,
    estimate_text_size,
)

ROOT = Path(__file__).resolve().parents[2]
CHALK_TEXT_DART = ROOT / "app/lib/blackboard/render/chalk_text.dart"
BOARD_METRICS_DART = ROOT / "app/lib/blackboard/model/board_metrics.dart"


def _constant(source: str, name: str) -> float:
    match = re.search(rf"static const double {name} = ([0-9.]+);", source)
    assert match is not None, f"missing {name} in Dart metrics"
    return float(match.group(1))


def _dart_specs() -> dict[str, TextStyleMetrics]:
    chalk = CHALK_TEXT_DART.read_text(encoding="utf-8")
    metrics = BOARD_METRICS_DART.read_text(encoding="utf-8")
    default_line_height = _constant(metrics, "lineHeight")
    glyph_factor = _constant(metrics, "glyphWidthFactor")
    default_letter_spacing = 0.01  # ChalkTextStyleSpec constructor default

    block_match = re.search(
        r"static const Map<String, ChalkTextStyleSpec> _specs =\s*"
        r"<String, ChalkTextStyleSpec>\{(.*?)\n  \};",
        chalk,
        re.DOTALL,
    )
    assert block_match is not None, "could not find Dart ChalkTextStyleSpec table"
    specs: dict[str, TextStyleMetrics] = {}
    for name, body in re.findall(
        r"'([A-Za-z0-9]+)':\s*ChalkTextStyleSpec\((.*?)\)\s*,",
        block_match.group(1),
        re.DOTALL,
    ):
        fraction = float(re.search(r"heightFraction:\s*([0-9.]+)", body).group(1))
        spacing_match = re.search(r"letterSpacingEm:\s*([0-9.]+)", body)
        line_match = re.search(
            r"lineHeight:\s*(?:BoardTextMetrics\.lineHeight|([0-9.]+))", body
        )
        monospace = "monospace: true" in body
        specs[name] = TextStyleMetrics(
            fraction=fraction,
            letter_spacing_em=(
                float(spacing_match.group(1))
                if spacing_match
                else default_letter_spacing
            ),
            line_height=(
                default_line_height if line_match and not line_match.group(1)
                else float(line_match.group(1)) if line_match and line_match.group(1)
                else default_line_height
            ),
            glyph_width_factor=0.62 if monospace else glyph_factor,
        )
    return specs


def test_backend_typography_table_matches_flutter_source():
    dart_specs = _dart_specs()
    assert set(dart_specs) == set(TEXT_STYLE_SPECS)
    for name, backend in TEXT_STYLE_SPECS.items():
        assert dart_specs[name] == backend, name


def _expected_lines(text: str, style: str, max_width: float) -> int:
    import math

    spec = TEXT_STYLE_SPECS[style]
    advance = spec.fraction * (spec.glyph_width_factor + spec.letter_spacing_em)
    natural = len(" ".join(text.split())) * advance
    return max(1, math.ceil(natural / min(max_width, DEFAULT_CONFIG.max_content_width)))


def test_h1_h6_wrapping_and_font_scale_use_the_contract():
    for level in range(1, 7):
        spec = TEXT_STYLE_SPECS[f"h{level}"]
        base = estimate_text_size("Photosynthesis", style="heading", heading_level=level)
        assert base[1] >= spec.fraction * spec.line_height
        assert base[1] <= spec.fraction * (spec.line_height + 0.5)
        scaled = estimate_text_size(
            "Photosynthesis", style="heading", heading_level=level, font_scale=2.0
        )
        assert scaled[1] > base[1] * 1.8
        assert scaled[0] >= base[0]


def test_long_wrapping_reserves_every_line_within_max_width():
    long_text = " ".join(["photosynthesis converts light into chemical energy"] * 4)
    width, height = estimate_text_size(long_text, style="body", max_width=0.35)
    lines = _expected_lines(long_text, "body", 0.35)
    spec = TEXT_STYLE_SPECS["body"]
    assert lines >= 8
    assert width <= 0.35 + 0.02
    assert height >= lines * spec.fraction * spec.line_height
    assert height <= DEFAULT_CONFIG.safe.h


def test_code_uses_monospace_contract():
    code = TEXT_STYLE_SPECS["code"]
    assert code.monospace if hasattr(code, "monospace") else code.glyph_width_factor == 0.62
    assert code.line_height > TEXT_STYLE_SPECS["body"].line_height
