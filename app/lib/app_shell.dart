import 'dart:math' as math;
import 'dart:ui';

import 'package:flutter/material.dart';

import 'core/config.dart';
import 'core/storage.dart';
import 'core/theme.dart';
import 'core/widgets/pwony_icons.dart';
import 'features/flashcards/flashcards_screen.dart';
import 'features/home/home_screen.dart';
import 'features/live/live_screen.dart';
import 'features/notes/notes_screen.dart';
import 'features/profile/profile_screen.dart';
import 'features/test_yourself/test_yourself_screen.dart';
import 'features/videos/videos_screen.dart';
import 'services/realtime_client.dart';

/// App shell: three primary destinations in a frosted liquid-glass pill dock,
/// plus a morphing circular orb that opens the monochrome feature popover.
class AppShell extends StatefulWidget {
  const AppShell({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  RealtimeClient? _realtimeClient;

  @override
  void dispose() {
    _realtimeClient?.dispose();
    super.dispose();
  }

  RealtimeClient _client() => _realtimeClient ??= RealtimeClient(
        config: widget.config,
        storage: widget.storage,
      );

  void _openLive(String lessonId) {
    _client().connect(lessonId: lessonId);
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => RealtimeClientScope(
          notifier: _client(),
          child: LiveScreen(client: _client()),
        ),
      ),
    );
  }

  void _selectFeature(int index) {
    final Widget target = switch (index) {
      0 => FlashcardsScreen(
          config: widget.config,
          storage: widget.storage,
        ),
      1 => VideosScreen(
          config: widget.config,
          storage: widget.storage,
        ),
      _ => TestYourselfScreen(
          config: widget.config,
          storage: widget.storage,
        ),
    };
    Navigator.of(context)
        .push(
      MaterialPageRoute<void>(builder: (_) => target),
    )
        .then((_) {
      if (mounted) setState(() {});
    });
  }

  /// The popover reports its selected action as a label; map it back to the
  /// existing feature navigation so routes stay untouched.
  void _onMenuActionSelected(String action) {
    final int? featureIndex = switch (action) {
      'Flashcards' => 0,
      'Discover Videos' => 1,
      'Test Yourself' => 2,
      _ => null,
    };
    if (featureIndex != null) _selectFeature(featureIndex);
  }

  @override
  Widget build(BuildContext context) {
    return RealtimeClientScope(
      notifier: _client(),
      child: Scaffold(
        backgroundColor: AppColors.background,
        extendBody: true,
        body: IndexedStack(
          index: _index,
          children: <Widget>[
            HomeScreen(
              config: widget.config,
              storage: widget.storage,
              onStartLesson: _openLive,
            ),
            NotesScreen(
              config: widget.config,
              storage: widget.storage,
              onContinueLesson: _openLive,
            ),
            ProfileScreen(
              config: widget.config,
              storage: widget.storage,
            ),
          ],
        ),
        bottomNavigationBar: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: SizedBox(
              height: 78,
              // Keeps the dock fully readable on narrow viewports by scaling
              // the whole bar down instead of clipping its active label.
              child: FittedBox(
                fit: BoxFit.scaleDown,
                alignment: Alignment.center,
                child: MonochromeLiquidGlassNavBar(
                  selectedIndex: _index,
                  onTabChanged: (value) => setState(() => _index = value),
                  onMenuActionSelected: _onMenuActionSelected,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Data model for each navigation tab in the liquid glass dock.
class LiquidNavItem {
  const LiquidNavItem({required this.label, required this.icon});

  final String label;
  final PwonyIconKind icon;
}

/// Borderless, matte-frosted monochrome liquid glass navigation dock paired
/// with an organically morphing circular menu orb.
class MonochromeLiquidGlassNavBar extends StatefulWidget {
  const MonochromeLiquidGlassNavBar({
    super.key,
    required this.selectedIndex,
    required this.onTabChanged,
    this.onMenuActionSelected,
    this.items = const <LiquidNavItem>[
      LiquidNavItem(label: 'Home', icon: PwonyIconKind.home),
      LiquidNavItem(label: 'Notes', icon: PwonyIconKind.notes),
      LiquidNavItem(label: 'Profile', icon: PwonyIconKind.profile),
    ],
    this.menuActions = const <String>[
      'Flashcards',
      'Discover Videos',
      'Test Yourself',
    ],
  });

  final int selectedIndex;
  final ValueChanged<int> onTabChanged;
  final ValueChanged<String>? onMenuActionSelected;
  final List<LiquidNavItem> items;
  final List<String> menuActions;

  @override
  State<MonochromeLiquidGlassNavBar> createState() =>
      _MonochromeLiquidGlassNavBarState();
}

class _MonochromeLiquidGlassNavBarState
    extends State<MonochromeLiquidGlassNavBar> with TickerProviderStateMixin {
  late final AnimationController _morphController;
  late final AnimationController _menuController;
  late final Animation<double> _iconProgress;
  late final Animation<double> _popoverFade;
  late final Animation<double> _popoverScale;

  bool _isMenuOpen = false;
  OverlayEntry? _popoverEntry;
  final LayerLink _orbLayerLink = LayerLink();

  @override
  void initState() {
    super.initState();
    // Continuous subtle organic morphing of the layer behind the orb.
    _morphController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 7500),
    )..repeat(reverse: true);

    // Hamburger-to-X morph and popover entrance.
    _menuController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 420),
    );
    _iconProgress = CurvedAnimation(
      parent: _menuController,
      curve: Curves.easeInOutCubic,
    );
    _popoverFade = CurvedAnimation(
      parent: _menuController,
      curve: Curves.easeOutCubic,
    );
    _popoverScale = Tween<double>(begin: 0.92, end: 1.0).animate(
      CurvedAnimation(parent: _menuController, curve: Curves.easeOutBack),
    );
  }

  @override
  void dispose() {
    _removePopover();
    _morphController.dispose();
    _menuController.dispose();
    super.dispose();
  }

  void _toggleMenu() {
    setState(() => _isMenuOpen = !_isMenuOpen);
    if (_isMenuOpen) {
      _menuController.forward();
      _showPopover();
    } else {
      _menuController.reverse();
      _removePopover();
    }
  }

  void _removePopover() {
    _popoverEntry?.remove();
    _popoverEntry = null;
  }

  void _showPopover() {
    _removePopover();
    _popoverEntry = OverlayEntry(
      builder: (context) => Stack(
        children: <Widget>[
          // Tap anywhere outside the popover to dismiss it.
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: _toggleMenu,
            ),
          ),
          CompositedTransformFollower(
            link: _orbLayerLink,
            showWhenUnlinked: false,
            targetAnchor: Alignment.topRight,
            followerAnchor: Alignment.bottomRight,
            offset: const Offset(0, -14),
            child: FadeTransition(
              opacity: _popoverFade,
              child: ScaleTransition(
                scale: _popoverScale,
                alignment: Alignment.bottomRight,
                child: Material(
                  color: Colors.transparent,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(28),
                    child: BackdropFilter(
                      filter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
                      child: Container(
                        width: 215,
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.06),
                          borderRadius: BorderRadius.circular(28),
                          boxShadow: <BoxShadow>[
                            BoxShadow(
                              color: Colors.black.withValues(alpha: 0.85),
                              blurRadius: 36,
                              offset: const Offset(0, 18),
                            ),
                          ],
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: <Widget>[
                            for (final action in widget.menuActions)
                              _buildPopoverItem(action),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
    Overlay.of(context).insert(_popoverEntry!);
  }

  Widget _buildPopoverItem(String title) {
    return InkWell(
      borderRadius: BorderRadius.circular(18),
      onTap: () {
        _toggleMenu();
        widget.onMenuActionSelected?.call(title);
      },
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text(
              title,
              style: TextStyle(
                color: Colors.white.withValues(alpha: 0.82),
                fontSize: 14,
                fontWeight: FontWeight.w500,
              ),
            ),
            PwonyIcon(
              PwonyIconKind.chevronRight,
              size: 16,
              color: Colors.white.withValues(alpha: 0.4),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: <Widget>[
        _buildFrostedDock(),
        const SizedBox(width: 16),
        _buildMenuOrb(),
      ],
    );
  }

  Widget _buildFrostedDock() {
    return ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 26, sigmaY: 26),
        child: Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.045),
            borderRadius: BorderRadius.circular(999),
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.9),
                blurRadius: 40,
                offset: const Offset(0, 20),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: List<Widget>.generate(widget.items.length, (index) {
              final LiquidNavItem item = widget.items[index];
              final bool isSelected = index == widget.selectedIndex;

              return Semantics(
                button: true,
                selected: isSelected,
                label: item.label,
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => widget.onTabChanged(index),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 520),
                    curve: Curves.easeOutBack,
                    height: 58,
                    padding: EdgeInsets.symmetric(
                      horizontal: isSelected ? 24 : 18,
                    ),
                    decoration: BoxDecoration(
                      color: isSelected
                          ? Colors.white.withValues(alpha: 0.14)
                          : Colors.transparent,
                      borderRadius: BorderRadius.circular(999),
                      boxShadow: isSelected
                          ? <BoxShadow>[
                              BoxShadow(
                                color: Colors.black.withValues(alpha: 0.45),
                                blurRadius: 16,
                                offset: const Offset(0, 6),
                              ),
                            ]
                          : null,
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        AnimatedScale(
                          scale: isSelected ? 1.06 : 1.0,
                          duration: const Duration(milliseconds: 420),
                          curve: Curves.easeOutBack,
                          child: PwonyIcon(
                            item.icon,
                            size: 23,
                            color: isSelected
                                ? Colors.white
                                : Colors.white.withValues(alpha: 0.52),
                          ),
                        ),
                        // Label expands only for the active tab.
                        AnimatedSize(
                          duration: const Duration(milliseconds: 460),
                          curve: Curves.easeOutBack,
                          child: isSelected
                              ? Padding(
                                  padding: const EdgeInsets.only(left: 10),
                                  child: Text(
                                    item.label,
                                    style: const TextStyle(
                                      color: Colors.white,
                                      fontSize: 15.5,
                                      fontWeight: FontWeight.w600,
                                      letterSpacing: -0.2,
                                    ),
                                  ),
                                )
                              : const SizedBox.shrink(),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            }),
          ),
        ),
      ),
    );
  }

  Widget _buildMenuOrb() {
    return CompositedTransformTarget(
      link: _orbLayerLink,
      child: SizedBox(
        width: 78,
        height: 78,
        child: Stack(
          alignment: Alignment.center,
          children: <Widget>[
            AnimatedBuilder(
              animation: _morphController,
              builder: (context, _) {
                return CustomPaint(
                  size: const Size(78, 78),
                  painter: _OrganicLiquidPainter(
                    progress: _morphController.value,
                    color: Colors.white.withValues(alpha: 0.045),
                  ),
                );
              },
            ),
            GestureDetector(
              onTap: _toggleMenu,
              child: Semantics(
                button: true,
                label: 'Toggle features',
                child: ClipOval(
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 350),
                      curve: Curves.easeOutBack,
                      width: 68,
                      height: 68,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: Colors.white.withValues(
                          alpha: _isMenuOpen ? 0.11 : 0.065,
                        ),
                        boxShadow: <BoxShadow>[
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.85),
                            blurRadius: 28,
                            offset: const Offset(0, 14),
                          ),
                        ],
                      ),
                      child: Center(
                        child: AnimatedBuilder(
                          animation: _iconProgress,
                          builder: (context, _) {
                            return CustomPaint(
                              size: const Size(26, 26),
                              painter: _HamburgerPainter(
                                progress: _iconProgress.value,
                                color: Colors.white,
                              ),
                            );
                          },
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Paints a smooth organic blob around the orb using sinusoidal waves.
class _OrganicLiquidPainter extends CustomPainter {
  const _OrganicLiquidPainter({
    required this.progress,
    required this.color,
  });

  final double progress;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;

    final Offset center = Offset(size.width / 2, size.height / 2);
    final double baseRadius = size.width * 0.46;
    final Path path = Path();

    const int points = 64;
    for (int i = 0; i <= points; i++) {
      final double angle = (i / points) * 2 * math.pi;
      final double wave1 =
          math.sin(angle * 3 + progress * 2 * math.pi) * 2.2;
      final double wave2 =
          math.cos(angle * 2 - progress * 2 * math.pi) * 1.8;
      final double radius = baseRadius + wave1 + wave2;

      final double x = center.dx + radius * math.cos(angle);
      final double y = center.dy + radius * math.sin(angle);

      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    path.close();
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _OrganicLiquidPainter oldDelegate) =>
      oldDelegate.progress != progress || oldDelegate.color != color;
}

/// Morphs the custom three-line hamburger glyph into a close (X) glyph.
/// 0.0 = menu, 1.0 = close; white stroke only, no glow or colored edges.
class _HamburgerPainter extends CustomPainter {
  const _HamburgerPainter({
    required this.progress,
    required this.color,
  });

  final double progress;
  final Color color;

  static Offset _lerp(Offset a, Offset b, double t) =>
      Offset(a.dx + (b.dx - a.dx) * t, a.dy + (b.dy - a.dy) * t);

  @override
  void paint(Canvas canvas, Size size) {
    final double t = progress.clamp(0.0, 1.0);
    final double strokeWidth = size.width * 0.06;
    final Paint paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    canvas.drawLine(
      _lerp(
        Offset(size.width * 0.30, size.height * 0.36),
        Offset(size.width * 0.35, size.height * 0.35),
        t,
      ),
      _lerp(
        Offset(size.width * 0.70, size.height * 0.36),
        Offset(size.width * 0.65, size.height * 0.65),
        t,
      ),
      paint,
    );

    final double middleAlpha = (1.0 - t).clamp(0.0, 1.0);
    if (middleAlpha > 0.01) {
      final Paint middlePaint = Paint()
        ..color = color.withValues(alpha: middleAlpha)
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round;
      final double shrink = 0.2 * t;
      canvas.drawLine(
        Offset(size.width * (0.30 + shrink), size.height * 0.50),
        Offset(size.width * (0.70 - shrink), size.height * 0.50),
        middlePaint,
      );
    }

    canvas.drawLine(
      _lerp(
        Offset(size.width * 0.30, size.height * 0.64),
        Offset(size.width * 0.65, size.height * 0.35),
        t,
      ),
      _lerp(
        Offset(size.width * 0.70, size.height * 0.64),
        Offset(size.width * 0.35, size.height * 0.65),
        t,
      ),
      paint,
    );
  }

  @override
  bool shouldRepaint(covariant _HamburgerPainter oldDelegate) =>
      oldDelegate.progress != progress || oldDelegate.color != color;
}
