import 'dart:ui' show Color;

/// Fixed chalk palette from CONTRACTS §1. The backend sends either a palette role
/// name or an explicit `#RRGGBB` string; both are accepted everywhere.
class ChalkPalette {
  const ChalkPalette._();

  /// Matte blackboard background (CONTRACTS fixture `board_background`).
  static const Color board = Color(0xFF171817);

  /// Slightly lighter frame/edge color around the board.
  static const Color frame = Color(0xFF2C2E2B);
  static const Color frameInner = Color(0xFF101110);

  static const Color primary = Color(0xFFF2F0E8);
  static const Color secondary = Color(0xFFA8A79E);
  static const Color blue = Color(0xFF9CC9E3);
  static const Color yellow = Color(0xFFE8D58C);
  static const Color red = Color(0xFFE39C9C);
  static const Color green = Color(0xFFA9D3A0);

  /// Hex alternatives for the palette roles (CONTRACTS §1 / fixture).
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

  /// Resolve a palette role name or an explicit `#RRGGBB` / `#AARRGGBB` string.
  /// Falls back to [fallback] for null/unknown input. Never throws.
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
