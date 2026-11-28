import 'dart:math' as math;
import 'dart:ui' show Color;

/// Chalk palette from CONTRACTS §1.
///
/// Ordinary teaching text uses the muted chalk roles below so prose stays
/// readable and distinguishable from illustrations. Diagrams, graphs and
/// generated drawings use [illustrationColors] exclusively: a black board can
/// never show dark, gray or off-white illustration ink. Both sides validate
/// colours (backend/app/board/palette.py mirrors this mapping).
class ChalkPalette {
  const ChalkPalette._();

  /// Matte blackboard background (true dark).
  static const Color board = Color(0xFF000000);

  /// Frame/edge color around the board (kept for compatibility; not drawn).
  static const Color frame = Color(0xFF000000);
  static const Color frameInner = Color(0xFF000000);

  // -- teaching text chalk --------------------------------------------------

  static const Color primary = Color(0xFFF2F0E8);
  static const Color secondary = Color(0xFFA8A79E);
  static const Color blue = Color(0xFF9CC9E3);
  static const Color yellow = Color(0xFFE8D58C);
  static const Color red = Color(0xFFE39C9C);
  static const Color green = Color(0xFFA9D3A0);

  /// Hex alternatives for the chalk roles (CONTRACTS §1 / fixture).
  static const Map<String, String> hexByRole = <String, String>{
    'primary': '#F2F0E8',
    'secondary': '#A8A79E',
    'blue': '#9CC9E3',
    'yellow': '#E8D58C',
    'red': '#E39C9C',
    'green': '#A9D3A0',
  };

  static const Map<String, Color> colorsByRole = <String, Color>{
    'primary': primary,
    'secondary': secondary,
    'blue': blue,
    'yellow': yellow,
    'red': red,
    'green': green,
  };

  // -- bright illustration palette ------------------------------------------

  /// Approved, high-contrast colours for every visual component on the board.
  static const Map<String, Color> illustrationColors = <String, Color>{
    'cyan': Color(0xFF00D9FF),
    'blue': Color(0xFF2979FF),
    'violet': Color(0xFFB388FF),
    'coral': Color(0xFFFF5252),
    'yellow': Color(0xFFFFD740),
    'lime': Color(0xFF76FF03),
    'orange': Color(0xFFFF9100),
    'pink': Color(0xFFFF80AB),
    'teal': Color(0xFF1DE9B6),
  };

  static const List<String> illustrationRoles = <String>[
    'cyan',
    'blue',
    'violet',
    'coral',
    'yellow',
    'lime',
    'orange',
    'pink',
    'teal',
  ];

  /// Legacy roles/names accepted from older events and prompts, remapped to a
  /// bright illustration role (never to the muted teaching chalk).
  static const Map<String, String> illustrationAliases = <String, String>{
    'primary': 'cyan',
    'secondary': 'teal',
    'blue': 'blue',
    'electric_blue': 'blue',
    'electricblue': 'blue',
    'yellow': 'yellow',
    'red': 'coral',
    'coral': 'coral',
    'green': 'lime',
    'lime': 'lime',
    'cyan': 'cyan',
    'violet': 'violet',
    'purple': 'violet',
    'orange': 'orange',
    'pink': 'pink',
    'teal': 'teal',
    'white': 'cyan',
    'black': 'cyan',
    'gray': 'teal',
    'grey': 'teal',
  };

  /// Deterministic complementary colour for index-based diagram parts.
  static Color illustrationForIndex(int index) =>
      illustrationColors[illustrationRoles[index % illustrationRoles.length]]!;

  /// True only for an exact approved illustration hex.
  static bool isApprovedIllustrationHex(Object? value) {
    if (value is! String) return false;
    final raw = value.trim().toUpperCase();
    return illustrationColors.values.any((color) => _hex(color) == raw);
  }

  /// Resolve a palette role or an explicit `#RRGGBB` / `#AARRGGBB` string for
  /// teaching text. Unknown values use [fallback]. Never throws.
  static Color resolve(Object? value, {Color fallback = primary}) {
    if (value == null) return fallback;
    if (value is Color) return value;
    final raw = value.toString().trim();
    if (raw.isEmpty) return fallback;
    final role = colorsByRole[raw.toLowerCase()];
    if (role != null) return role;
    final hex = raw.startsWith('#') ? raw.substring(1) : raw;
    if (hex.length == 6 || hex.length == 8) {
      final parsed = int.tryParse(hex, radix: 16);
      if (parsed != null) {
        return Color(hex.length == 6 ? 0xFF000000 | parsed : parsed);
      }
    }
    return fallback;
  }

  /// Resolve an illustration colour safely.
  ///
  /// Bright palette roles pass through. Legacy chalk roles map to their bright
  /// counterpart. Any Qwen-provided hex is snapped to the closest approved
  /// bright colour, so black/near-black/gray/off-white/dark input can never be
  /// rendered as diagram ink. Never throws.
  static Color resolveIllustration(
    Object? value, {
    String fallbackRole = 'cyan',
  }) {
    final fallback = illustrationColors[fallbackRole] ?? illustrationColors['cyan']!;
    if (value is Color) {
      return _nearestIllustration(value);
    }
    if (value == null) return fallback;
    final raw = value.toString().trim();
    if (raw.isEmpty) return fallback;
    final alias = illustrationAliases[raw.toLowerCase()];
    if (alias != null) return illustrationColors[alias]!;
    final approved = illustrationColors[raw.toUpperCase()];
    if (approved != null) return approved;
    final hex = raw.startsWith('#') ? raw.substring(1) : raw;
    final parsed = hex.length == 6 ? int.tryParse(hex, radix: 16) : null;
    if (parsed == null) return fallback;
    return _nearestIllustration(Color(0xFF000000 | parsed));
  }

  static Color _nearestIllustration(Color color) {
    var best = illustrationColors['cyan']!;
    var bestDistance = double.infinity;
    for (final candidate in illustrationColors.values) {
      // Weighted RGB distance approximates perceived brightness; green weighs
      // most, blue least. Every candidate is already bright.
      final distance = 2.0 * math.pow(color.r - candidate.r, 2) +
          3.0 * math.pow(color.g - candidate.g, 2) +
          math.pow(color.b - candidate.b, 2);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = candidate;
      }
    }
    return best;
  }

  static String _hex(Color color) =>
      '#${(color.toARGB32() & 0xFFFFFF).toRadixString(16).toUpperCase().padLeft(6, '0')}';

  /// Resolve with an alpha multiplier (chalk strokes are slightly translucent).
  static Color withOpacity(Color color, double opacity) =>
      color.withValues(alpha: color.a * opacity.clamp(0.0, 1.0));

  /// Named color roles that can be used verbatim by renderers.
  static const List<Color> all = <Color>[
    primary,
    secondary,
    blue,
    yellow,
    red,
    green,
  ];
}
