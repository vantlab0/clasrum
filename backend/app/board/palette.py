"""Bright illustration palette shared by every board tool boundary.

Teaching text keeps the muted chalk palette (``app.db.constants.CHALK_COLORS``)
so prose stays distinguishable from illustrations.  Diagrams, graphs and
generated visuals are different: on a black board any dark, gray or off-white
ink is invisible or muddy.  Every illustration colour entering the system is
therefore *sanitized here*, at the server boundary, and snapped to an approved
high-contrast colour.  Qwen is never trusted to supply an arbitrary hex value.
"""
from __future__ import annotations

from typing import Any

# Bright, saturated colours approved for diagrams/illustrations on black chalk.
ILLUSTRATION_COLORS: dict[str, str] = {
    "cyan": "#00D9FF",
    "blue": "#2979FF",
    "violet": "#B388FF",
    "coral": "#FF5252",
    "yellow": "#FFD740",
    "lime": "#76FF03",
    "orange": "#FF9100",
    "pink": "#FF80AB",
    "teal": "#1DE9B6",
}

# Legacy chalk roles and natural-language aliases still accepted by older
# clients/prompts.  They are remapped to a bright illustration role.
ILLUSTRATION_ALIASES: dict[str, str] = {
    "primary": "cyan",
    "secondary": "teal",
    "blue": "blue",
    "electric_blue": "blue",
    "electricblue": "blue",
    "yellow": "yellow",
    "red": "coral",
    "coral": "coral",
    "green": "lime",
    "lime": "lime",
    "cyan": "cyan",
    "violet": "violet",
    "purple": "violet",
    "orange": "orange",
    "pink": "pink",
    "teal": "teal",
    "white": "cyan",
    "black": "cyan",
    "gray": "teal",
    "grey": "teal",
}

_ROLE_BY_HEX = {hex_value.upper(): role for role, hex_value in ILLUSTRATION_COLORS.items()}
_ROLES = tuple(ILLUSTRATION_COLORS)


def _parse_hex(value: str) -> tuple[int, int, int] | None:
    if len(value) != 7 or not value.startswith("#"):
        return None
    try:
        return (
            int(value[1:3], 16),
            int(value[3:5], 16),
            int(value[5:7], 16),
        )
    except ValueError:
        return None


def _nearest_role(rgb: tuple[int, int, int]) -> str:
    """Snap an untrusted colour to the closest approved bright role.

    Weighted Euclidean distance approximates perceived brightness (green
    contributes most, blue least).  Every candidate is already bright, so a
    dark/gray input can never survive this step.
    """
    best_role = "cyan"
    best_distance = float("inf")
    for role in _ROLES:
        target = _parse_hex(ILLUSTRATION_COLORS[role])
        assert target is not None
        distance = sum(
            weight * (float(a) - float(b)) ** 2
            for weight, a, b in zip((2.0, 3.0, 1.0), rgb, target)
        )
        if distance < best_distance:
            best_distance = distance
            best_role = role
    return best_role


def sanitize_illustration_color(value: Any, default: str = "cyan") -> str:
    """Return an approved ``#RRGGBB`` illustration colour.

    Accepts bright palette role names, legacy chalk role names, colour aliases
    and arbitrary ``#RRGGBB`` strings.  Anything unrecognised falls back to
    ``default``; any hex is snapped to the nearest approved palette role, so
    black/near-black/gray/off-white/dark colours are impossible in the output.
    """
    fallback_role = ILLUSTRATION_ALIASES.get(str(default).strip().lower(), "cyan")
    if not isinstance(value, str):
        return ILLUSTRATION_COLORS[fallback_role]
    text = value.strip()
    lowered = text.lower()
    role = ILLUSTRATION_ALIASES.get(lowered)
    if role is not None:
        return ILLUSTRATION_COLORS[role]
    rgb = _parse_hex(text)
    if rgb is None:
        return ILLUSTRATION_COLORS[fallback_role]
    exact = _ROLE_BY_HEX.get(text.upper())
    if exact is not None:
        return ILLUSTRATION_COLORS[exact]
    return ILLUSTRATION_COLORS[_nearest_role(rgb)]


def is_approved_illustration_color(value: Any) -> bool:
    """True only for an exact approved palette hex (upper/lower case agnostic)."""
    return isinstance(value, str) and value.strip().upper() in _ROLE_BY_HEX


def contrast_ratio_against_board(value: str) -> float:
    """WCAG-like contrast ratio of an illustration colour against black.

    Used by tests/diagnostics to prove a colour is not a low-contrast choice.
    """
    rgb = _parse_hex(value)
    if rgb is None:
        return 1.0
    channels = []
    for channel in rgb:
        c = channel / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    return (luminance + 0.05) / 0.05


__all__ = [
    "ILLUSTRATION_COLORS",
    "ILLUSTRATION_ALIASES",
    "sanitize_illustration_color",
    "is_approved_illustration_color",
    "contrast_ratio_against_board",
]
