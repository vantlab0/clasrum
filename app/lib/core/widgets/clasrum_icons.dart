import 'dart:math' as math;

import 'package:flutter/widgets.dart';

/// Clasrum custom icon set — consistent geometric, rounded-stroke glyphs.
/// Do not use Material Icons or emoji as substitutes for product UI icons.
enum ClasrumIconKind {
  home,
  notes,
  profile,
  back,
  search,
  startLearning,
  microphone,
  pause,
  resume,
  endLesson,
  settings,
  upgradePro,
  logout,
  editProfile,
  notifications,
  passwordSecurity,
  close,
  moreOptions,
  // Additional UI glyphs for consistent replacement of generic icons.
  chevronDown,
  chevronRight,
  email,
  lock,
  visibility,
  visibilityOff,
  info,
  delete,
  transcript,
  interrupt,
}

/// Reusable custom icon widget with consistent sizing and optical alignment.
class ClasrumIcon extends StatelessWidget {
  const ClasrumIcon(
    this.kind, {
    super.key,
    this.size = 22,
    this.color,
    this.strokeWidth,
  });

  final ClasrumIconKind kind;
  final double size;
  final Color? color;
  final double? strokeWidth;

  @override
  Widget build(BuildContext context) {
    final iconColor = color ?? const Color(0xFFFFFFFF);
    final sw = strokeWidth ?? (size >= 24 ? 1.7 : 1.6);
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: _ClasrumIconPainter(
          kind: kind,
          color: iconColor,
          strokeWidth: sw,
        ),
      ),
    );
  }
}

class _ClasrumIconPainter extends CustomPainter {
  const _ClasrumIconPainter({
    required this.kind,
    required this.color,
    required this.strokeWidth,
  });

  final ClasrumIconKind kind;
  final Color color;
  final double strokeWidth;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width;
    final c = Offset(s / 2, s / 2);
    final stroke = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..isAntiAlias = true;
    final fill = Paint()
      ..color = color
      ..style = PaintingStyle.fill
      ..isAntiAlias = true;

    canvas.save();
    // Subtle optical adjustments per glyph.
    switch (kind) {
      case ClasrumIconKind.home:
        canvas.translate(0, 0.2);
        break;
      case ClasrumIconKind.notes:
        canvas.translate(0.2, 0);
        break;
      case ClasrumIconKind.profile:
        canvas.translate(0, 0.2);
        break;
      case ClasrumIconKind.back:
        canvas.translate(0.6, 0);
        break;
      case ClasrumIconKind.search:
        canvas.translate(-0.2, -0.2);
        break;
      case ClasrumIconKind.startLearning:
        canvas.translate(0.4, 0);
        break;
      case ClasrumIconKind.microphone:
        canvas.translate(0, -0.2);
        break;
      case ClasrumIconKind.endLesson:
        canvas.translate(0, 0.2);
        break;
      default:
        break;
    }

    switch (kind) {
      case ClasrumIconKind.home:
        _home(canvas, s, stroke);
        break;
      case ClasrumIconKind.notes:
        _notes(canvas, s, stroke);
        break;
      case ClasrumIconKind.profile:
        _profile(canvas, s, stroke);
        break;
      case ClasrumIconKind.back:
        _back(canvas, s, stroke);
        break;
      case ClasrumIconKind.search:
        _search(canvas, s, stroke);
        break;
      case ClasrumIconKind.startLearning:
        _startLearning(canvas, s, stroke, fill);
        break;
      case ClasrumIconKind.microphone:
        _microphone(canvas, s, stroke);
        break;
      case ClasrumIconKind.pause:
        _pause(canvas, s, stroke);
        break;
      case ClasrumIconKind.resume:
        _resume(canvas, s, stroke, fill);
        break;
      case ClasrumIconKind.endLesson:
        _endLesson(canvas, s, stroke);
        break;
      case ClasrumIconKind.settings:
        _settings(canvas, s, stroke, c);
        break;
      case ClasrumIconKind.upgradePro:
        _upgradePro(canvas, s, stroke);
        break;
      case ClasrumIconKind.logout:
        _logout(canvas, s, stroke);
        break;
      case ClasrumIconKind.editProfile:
        _editProfile(canvas, s, stroke);
        break;
      case ClasrumIconKind.notifications:
        _notifications(canvas, s, stroke);
        break;
      case ClasrumIconKind.passwordSecurity:
        _passwordSecurity(canvas, s, stroke);
        break;
      case ClasrumIconKind.close:
        _close(canvas, s, stroke);
        break;
      case ClasrumIconKind.moreOptions:
        _moreOptions(canvas, s, fill);
        break;
      case ClasrumIconKind.chevronDown:
        _chevronDown(canvas, s, stroke);
        break;
      case ClasrumIconKind.chevronRight:
        _chevronRight(canvas, s, stroke);
        break;
      case ClasrumIconKind.email:
        _email(canvas, s, stroke);
        break;
      case ClasrumIconKind.lock:
        _passwordSecurity(canvas, s, stroke);
        break;
      case ClasrumIconKind.visibility:
        _visibility(canvas, s, stroke);
        break;
      case ClasrumIconKind.visibilityOff:
        _visibilityOff(canvas, s, stroke);
        break;
      case ClasrumIconKind.info:
        _info(canvas, s, stroke, fill);
        break;
      case ClasrumIconKind.delete:
        _delete(canvas, s, stroke);
        break;
      case ClasrumIconKind.transcript:
        _transcript(canvas, s, stroke);
        break;
      case ClasrumIconKind.interrupt:
        _interrupt(canvas, s, stroke);
        break;
    }
    canvas.restore();
  }

  void _home(Canvas canvas, double s, Paint p) {
    final path = Path()
      ..moveTo(s * 0.2, s * 0.46)
      ..lineTo(s * 0.5, s * 0.22)
      ..lineTo(s * 0.8, s * 0.46)
      ..lineTo(s * 0.8, s * 0.78)
      ..quadraticBezierTo(s * 0.8, s * 0.82, s * 0.76, s * 0.82)
      ..lineTo(s * 0.58, s * 0.82)
      ..lineTo(s * 0.58, s * 0.58)
      ..lineTo(s * 0.42, s * 0.58)
      ..lineTo(s * 0.42, s * 0.82)
      ..lineTo(s * 0.24, s * 0.82)
      ..quadraticBezierTo(s * 0.2, s * 0.82, s * 0.2, s * 0.78)
      ..close();
    canvas.drawPath(path, p);
  }

  void _notes(Canvas canvas, double s, Paint p) {
    final r = RRect.fromRectAndRadius(
      Rect.fromLTWH(s * 0.24, s * 0.18, s * 0.52, s * 0.64),
      Radius.circular(s * 0.08),
    );
    canvas.drawRRect(r, p);
    canvas.drawLine(Offset(s * 0.36, s * 0.38), Offset(s * 0.64, s * 0.38), p);
    canvas.drawLine(Offset(s * 0.36, s * 0.5), Offset(s * 0.64, s * 0.5), p);
    canvas.drawLine(Offset(s * 0.36, s * 0.62), Offset(s * 0.54, s * 0.62), p);
  }

  void _profile(Canvas canvas, double s, Paint p) {
    canvas.drawCircle(Offset(s * 0.5, s * 0.34), s * 0.12, p);
    final path = Path()
      ..moveTo(s * 0.24, s * 0.78)
      ..cubicTo(s * 0.28, s * 0.58, s * 0.38, s * 0.52, s * 0.5, s * 0.52)
      ..cubicTo(s * 0.62, s * 0.52, s * 0.72, s * 0.58, s * 0.76, s * 0.78);
    canvas.drawPath(path, p);
  }

  void _back(Canvas canvas, double s, Paint p) {
    final path = Path()
      ..moveTo(s * 0.58, s * 0.24)
      ..lineTo(s * 0.36, s * 0.5)
      ..lineTo(s * 0.58, s * 0.76);
    canvas.drawPath(path, p);
  }

  void _search(Canvas canvas, double s, Paint p) {
    canvas.drawCircle(Offset(s * 0.45, s * 0.45), s * 0.17, p);
    canvas.drawLine(
      Offset(s * 0.57, s * 0.57),
      Offset(s * 0.76, s * 0.76),
      p,
    );
  }

  void _startLearning(Canvas canvas, double s, Paint p, Paint fill) {
    // Refined compass/spark cue: circular focus with forward play tip.
    canvas.drawCircle(Offset(s * 0.4, s * 0.5), s * 0.17, p);
    final tip = Path()
      ..moveTo(s * 0.56, s * 0.38)
      ..lineTo(s * 0.74, s * 0.5)
      ..lineTo(s * 0.56, s * 0.62)
      ..close();
    final tipPaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill
      ..isAntiAlias = true;
    canvas.drawPath(tip, tipPaint);
    // Subtle spark
    canvas.drawLine(Offset(s * 0.28, s * 0.26), Offset(s * 0.34, s * 0.32), p);
    canvas.drawLine(Offset(s * 0.22, s * 0.34), Offset(s * 0.29, s * 0.36), p);
  }

  void _microphone(Canvas canvas, double s, Paint p) {
    final body = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(s * 0.5, s * 0.42),
        width: s * 0.22,
        height: s * 0.34,
      ),
      Radius.circular(s * 0.11),
    );
    canvas.drawRRect(body, p);
    final arc = Path()
      ..moveTo(s * 0.28, s * 0.44)
      ..quadraticBezierTo(s * 0.28, s * 0.64, s * 0.5, s * 0.64)
      ..quadraticBezierTo(s * 0.72, s * 0.64, s * 0.72, s * 0.44);
    canvas.drawPath(arc, p);
    canvas.drawLine(Offset(s * 0.5, s * 0.64), Offset(s * 0.5, s * 0.76), p);
    canvas.drawLine(Offset(s * 0.38, s * 0.76), Offset(s * 0.62, s * 0.76), p);
  }

  void _pause(Canvas canvas, double s, Paint p) {
    final r = Radius.circular(s * 0.045);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(s * 0.35, s * 0.29, s * 0.095, s * 0.42),
        r,
      ),
      p,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(s * 0.555, s * 0.29, s * 0.095, s * 0.42),
        r,
      ),
      p,
    );
  }

  void _resume(Canvas canvas, double s, Paint p, Paint fill) {
    final path = Path()
      ..moveTo(s * 0.39, s * 0.29)
      ..lineTo(s * 0.66, s * 0.5)
      ..lineTo(s * 0.39, s * 0.71)
      ..close();
    canvas.drawPath(path, p);
  }

  void _endLesson(Canvas canvas, double s, Paint p) {
    // Refined end-session handset glyph.
    final path = Path()
      ..moveTo(s * 0.24, s * 0.6)
      ..cubicTo(s * 0.32, s * 0.5, s * 0.42, s * 0.48, s * 0.5, s * 0.52)
      ..cubicTo(s * 0.58, s * 0.48, s * 0.68, s * 0.5, s * 0.76, s * 0.6);
    canvas.drawPath(path, p);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: Offset(s * 0.24, s * 0.64),
          width: s * 0.14,
          height: s * 0.09,
        ),
        Radius.circular(s * 0.04),
      ),
      p,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: Offset(s * 0.76, s * 0.64),
          width: s * 0.14,
          height: s * 0.09,
        ),
        Radius.circular(s * 0.04),
      ),
      p,
    );
  }

  void _settings(Canvas canvas, double s, Paint p, Offset c) {
    canvas.drawCircle(c, s * 0.085, p);
    canvas.drawCircle(c, s * 0.22, p);
    for (var i = 0; i < 6; i++) {
      final a = (math.pi / 3) * i - math.pi / 2;
      final inner = Offset(c.dx + math.cos(a) * s * 0.22, c.dy + math.sin(a) * s * 0.22);
      final outer = Offset(c.dx + math.cos(a) * s * 0.285, c.dy + math.sin(a) * s * 0.285);
      canvas.drawLine(inner, outer, p);
    }
  }

  void _upgradePro(Canvas canvas, double s, Paint p) {
    // Faceted premium gem/star mark.
    final path = Path()
      ..moveTo(s * 0.5, s * 0.2)
      ..lineTo(s * 0.61, s * 0.39)
      ..lineTo(s * 0.8, s * 0.43)
      ..lineTo(s * 0.65, s * 0.56)
      ..lineTo(s * 0.69, s * 0.76)
      ..lineTo(s * 0.5, s * 0.65)
      ..lineTo(s * 0.31, s * 0.76)
      ..lineTo(s * 0.35, s * 0.56)
      ..lineTo(s * 0.2, s * 0.43)
      ..lineTo(s * 0.39, s * 0.39)
      ..close();
    canvas.drawPath(path, p);
  }

  void _logout(Canvas canvas, double s, Paint p) {
    final frame = Path()
      ..moveTo(s * 0.52, s * 0.22)
      ..lineTo(s * 0.3, s * 0.22)
      ..quadraticBezierTo(s * 0.24, s * 0.22, s * 0.24, s * 0.28)
      ..lineTo(s * 0.24, s * 0.72)
      ..quadraticBezierTo(s * 0.24, s * 0.78, s * 0.3, s * 0.78)
      ..lineTo(s * 0.52, s * 0.78);
    canvas.drawPath(frame, p);
    canvas.drawLine(Offset(s * 0.42, s * 0.5), Offset(s * 0.76, s * 0.5), p);
    final arrow = Path()
      ..moveTo(s * 0.64, s * 0.36)
      ..lineTo(s * 0.76, s * 0.5)
      ..lineTo(s * 0.64, s * 0.64);
    canvas.drawPath(arrow, p);
  }

  void _editProfile(Canvas canvas, double s, Paint p) {
    canvas.drawCircle(Offset(s * 0.36, s * 0.38), s * 0.1, p);
    final path = Path()
      ..moveTo(s * 0.18, s * 0.74)
      ..cubicTo(s * 0.22, s * 0.58, s * 0.28, s * 0.54, s * 0.36, s * 0.54)
      ..cubicTo(s * 0.44, s * 0.54, s * 0.5, s * 0.58, s * 0.54, s * 0.66);
    canvas.drawPath(path, p);
    // pencil
    canvas.drawLine(Offset(s * 0.52, s * 0.74), Offset(s * 0.76, s * 0.5), p);
    canvas.drawLine(Offset(s * 0.7, s * 0.44), Offset(s * 0.82, s * 0.56), p);
  }

  void _notifications(Canvas canvas, double s, Paint p) {
    final bell = Path()
      ..moveTo(s * 0.5, s * 0.22)
      ..cubicTo(s * 0.38, s * 0.22, s * 0.32, s * 0.32, s * 0.32, s * 0.44)
      ..lineTo(s * 0.32, s * 0.58)
      ..lineTo(s * 0.26, s * 0.66)
      ..lineTo(s * 0.74, s * 0.66)
      ..lineTo(s * 0.68, s * 0.58)
      ..lineTo(s * 0.68, s * 0.44)
      ..cubicTo(s * 0.68, s * 0.32, s * 0.62, s * 0.22, s * 0.5, s * 0.22)
      ..close();
    canvas.drawPath(bell, p);
    canvas.drawArc(
      Rect.fromCenter(center: Offset(s * 0.5, s * 0.7), width: s * 0.16, height: s * 0.12),
      0.15,
      math.pi - 0.3,
      false,
      p,
    );
  }

  void _passwordSecurity(Canvas canvas, double s, Paint p) {
    final body = RRect.fromRectAndRadius(
      Rect.fromLTWH(s * 0.26, s * 0.44, s * 0.48, s * 0.34),
      Radius.circular(s * 0.07),
    );
    canvas.drawRRect(body, p);
    final shackle = Path()
      ..moveTo(s * 0.34, s * 0.44)
      ..lineTo(s * 0.34, s * 0.36)
      ..cubicTo(s * 0.34, s * 0.24, s * 0.42, s * 0.2, s * 0.5, s * 0.2)
      ..cubicTo(s * 0.58, s * 0.2, s * 0.66, s * 0.24, s * 0.66, s * 0.36)
      ..lineTo(s * 0.66, s * 0.44);
    canvas.drawPath(shackle, p);
    canvas.drawCircle(Offset(s * 0.5, s * 0.6), s * 0.035, Paint()..color = color..style = PaintingStyle.fill);
  }

  void _close(Canvas canvas, double s, Paint p) {
    canvas.drawLine(Offset(s * 0.3, s * 0.3), Offset(s * 0.7, s * 0.7), p);
    canvas.drawLine(Offset(s * 0.7, s * 0.3), Offset(s * 0.3, s * 0.7), p);
  }

  void _moreOptions(Canvas canvas, double s, Paint fill) {
    final r = s * 0.05;
    for (final x in [0.32, 0.5, 0.68]) {
      canvas.drawCircle(Offset(s * x, s * 0.5), r, fill);
    }
  }

  void _chevronDown(Canvas canvas, double s, Paint p) {
    final path = Path()
      ..moveTo(s * 0.32, s * 0.42)
      ..lineTo(s * 0.5, s * 0.6)
      ..lineTo(s * 0.68, s * 0.42);
    canvas.drawPath(path, p);
  }

  void _chevronRight(Canvas canvas, double s, Paint p) {
    final path = Path()
      ..moveTo(s * 0.42, s * 0.3)
      ..lineTo(s * 0.6, s * 0.5)
      ..lineTo(s * 0.42, s * 0.7);
    canvas.drawPath(path, p);
  }

  void _email(Canvas canvas, double s, Paint p) {
    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(s * 0.2, s * 0.28, s * 0.6, s * 0.44),
      Radius.circular(s * 0.07),
    );
    canvas.drawRRect(rect, p);
    final flap = Path()
      ..moveTo(s * 0.22, s * 0.32)
      ..lineTo(s * 0.5, s * 0.52)
      ..lineTo(s * 0.78, s * 0.32);
    canvas.drawPath(flap, p);
  }

  void _visibility(Canvas canvas, double s, Paint p) {
    final eye = Path()
      ..moveTo(s * 0.18, s * 0.5)
      ..quadraticBezierTo(s * 0.5, s * 0.26, s * 0.82, s * 0.5)
      ..quadraticBezierTo(s * 0.5, s * 0.74, s * 0.18, s * 0.5);
    canvas.drawPath(eye, p);
    canvas.drawCircle(Offset(s * 0.5, s * 0.5), s * 0.08, p);
  }

  void _visibilityOff(Canvas canvas, double s, Paint p) {
    _visibility(canvas, s, p);
    canvas.drawLine(Offset(s * 0.24, s * 0.76), Offset(s * 0.76, s * 0.24), p);
  }

  void _info(Canvas canvas, double s, Paint p, Paint fill) {
    canvas.drawCircle(Offset(s * 0.5, s * 0.5), s * 0.28, p);
    canvas.drawCircle(Offset(s * 0.5, s * 0.38), s * 0.03, fill);
    canvas.drawLine(Offset(s * 0.5, s * 0.46), Offset(s * 0.5, s * 0.64), p);
  }

  void _delete(Canvas canvas, double s, Paint p) {
    canvas.drawLine(Offset(s * 0.28, s * 0.32), Offset(s * 0.72, s * 0.32), p);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(s * 0.32, s * 0.32, s * 0.36, s * 0.42),
        Radius.circular(s * 0.05),
      ),
      p,
    );
    canvas.drawLine(Offset(s * 0.42, s * 0.24), Offset(s * 0.58, s * 0.24), p);
    canvas.drawLine(Offset(s * 0.44, s * 0.44), Offset(s * 0.44, s * 0.62), p);
    canvas.drawLine(Offset(s * 0.56, s * 0.44), Offset(s * 0.56, s * 0.62), p);
  }

  void _transcript(Canvas canvas, double s, Paint p) {
    canvas.drawLine(Offset(s * 0.26, s * 0.32), Offset(s * 0.74, s * 0.32), p);
    canvas.drawLine(Offset(s * 0.26, s * 0.46), Offset(s * 0.66, s * 0.46), p);
    canvas.drawLine(Offset(s * 0.26, s * 0.6), Offset(s * 0.74, s * 0.6), p);
    canvas.drawLine(Offset(s * 0.26, s * 0.74), Offset(s * 0.58, s * 0.74), p);
  }

  void _interrupt(Canvas canvas, double s, Paint p) {
    // raised hand / stop-interrupt gesture, geometric
    final palm = RRect.fromRectAndRadius(
      Rect.fromLTWH(s * 0.34, s * 0.4, s * 0.32, s * 0.34),
      Radius.circular(s * 0.08),
    );
    canvas.drawRRect(palm, p);
    for (final x in [0.38, 0.48, 0.58]) {
      canvas.drawLine(Offset(s * x, s * 0.4), Offset(s * x, s * 0.26), p);
    }
  }

  @override
  bool shouldRepaint(covariant _ClasrumIconPainter oldDelegate) {
    return oldDelegate.kind != kind ||
        oldDelegate.color != color ||
        oldDelegate.strokeWidth != strokeWidth;
  }
}

/// Animated nav icon wrapper: subtle scale/slide/fade on selection changes.
class AnimatedClasrumNavIcon extends StatelessWidget {
  const AnimatedClasrumNavIcon({
    super.key,
    required this.kind,
    required this.selected,
    required this.color,
    this.size = 22,
  });

  final ClasrumIconKind kind;
  final bool selected;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) {
    return AnimatedScale(
      duration: const Duration(milliseconds: 240),
      curve: Curves.easeOutCubic,
      scale: selected ? 1.0 : 0.96,
      child: AnimatedSlide(
        duration: const Duration(milliseconds: 240),
        curve: Curves.easeOutCubic,
        offset: selected ? Offset.zero : const Offset(0, 0.02),
        child: AnimatedOpacity(
          duration: const Duration(milliseconds: 200),
          opacity: 1.0,
          child: ClasrumIcon(kind, size: size, color: color),
        ),
      ),
    );
  }
}