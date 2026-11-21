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

/// App shell: exactly three destinations on the floating pill, with a second
/// floating feature menu for the AI tools reachable from the same surface.
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
  bool _menuOpen = false;
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

  void _toggleMenu() {
    setState(() => _menuOpen = !_menuOpen);
  }

  void _selectFeature(int index) {
    setState(() => _menuOpen = false);
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

  @override
  Widget build(BuildContext context) {
    return RealtimeClientScope(
      notifier: _client(),
      child: Scaffold(
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
            child: LayoutBuilder(
              builder: (context, constraints) {
                final Widget pill = _FloatingNavBar(
                  selectedIndex: _index,
                  onSelected: (value) => setState(() => _index = value),
                );
                const double gap = 16;
                const double buttonSize = 72;
                const double pillWidth = 264; // slot(88) * 3
                return SizedBox(
                  height: 90,
                  child: Stack(
                    clipBehavior: Clip.none,
                    children: <Widget>[
                      // Nav pill: never affected by the menu panel's width;
                      // only scales down when there isn't room for pill + gap + button.
                      Positioned(
                        left: 0,
                        top: 0,
                        bottom: 0,
                        child: constraints.maxWidth >= pillWidth + gap + buttonSize
                            ? pill
                            : FittedBox(
                                fit: BoxFit.scaleDown,
                                alignment: Alignment.centerLeft,
                                child: pill,
                              ),
                      ),
                      // Hamburger button, anchored to the right edge.
                      Positioned(
                        right: 0,
                        top: (90 - buttonSize) / 2,
                        child: _CircularHamburgerButton(
                          isMenuOpen: _menuOpen,
                          onPressed: _toggleMenu,
                          size: buttonSize,
                        ),
                      ),
                      // Feature menu floats above the hamburger button and
                      // overlays the body — the nav pill keeps its size.
                      // Slow, noticeable slide + fade + scale transition.
                      Positioned(
                        right: 0,
                        bottom: 102,
                        child: IgnorePointer(
                          ignoring: !_menuOpen,
                          child: AnimatedSlide(
                            duration: const Duration(milliseconds: 950),
                            curve: Curves.easeOutCubic,
                            offset: _menuOpen
                                ? Offset.zero
                                : const Offset(0, 0.45),
                            child: AnimatedOpacity(
                              duration: const Duration(milliseconds: 950),
                              curve: Curves.easeOutCubic,
                              opacity: _menuOpen ? 1.0 : 0.0,
                              child: AnimatedScale(
                                duration: const Duration(milliseconds: 950),
                                curve: Curves.easeOutBack,
                                alignment: Alignment.bottomRight,
                                scale: _menuOpen ? 1.0 : 0.6,
                                child: ExcludeFocus(
                                  excluding: !_menuOpen,
                                  child: _FeatureMenu(
                                    onItemSelected: _selectFeature,
                                    animateIn: _menuOpen,
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
              },
            ),
          ),
        ),
      ),
    );
  }
}

// ── Left-positioned navigation pill ─────────────────────────────────────────
class _FloatingNavBar extends StatelessWidget {
  const _FloatingNavBar({
    required this.selectedIndex,
    required this.onSelected,
  });

  final int selectedIndex;
  final ValueChanged<int> onSelected;

  static const List<_NavItem> _items = <_NavItem>[
    _NavItem('Home', PwonyIconKind.home),
    _NavItem('Notes', PwonyIconKind.notes),
    _NavItem('Profile', PwonyIconKind.profile),
  ];

  @override
  Widget build(BuildContext context) {
    const double slot = 88;
    const double containerWidth = slot * 3;
    const double height = 90;
    const double pad = 9;

    return SizedBox(
      width: containerWidth,
      height: height,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(height / 2),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: Container(
            padding: const EdgeInsets.all(pad),
            decoration: BoxDecoration(
              color: AppColors.surface.withValues(alpha: 0.55),
              borderRadius: BorderRadius.circular(height / 2),
              border: Border.all(
                color: AppColors.border.withValues(alpha: 0.55),
              ),
            ),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final double unit = constraints.maxWidth / 3;

                double leftFor(int index) {
                  double left = 0;
                  for (var i = 0; i < index; i++) {
                    left += i == selectedIndex ? 1.5 * unit : 0.75 * unit;
                  }
                  return left;
                }

                return Stack(
                  children: <Widget>[
                    AnimatedPositioned(
                      duration: const Duration(milliseconds: 520),
                      curve: Curves.easeInOutCubic,
                      left: leftFor(selectedIndex),
                      top: 0,
                      bottom: 0,
                      width: 1.5 * unit,
                      child: Container(
                        decoration: BoxDecoration(
                          color: AppColors.blue,
                          borderRadius: BorderRadius.circular(999),
                        ),
                      ),
                    ),
                    Row(
                      children: List<Widget>.generate(3, (index) {
                        final bool selected = index == selectedIndex;
                        final double width =
                            selected ? 1.5 * unit : 0.75 * unit;
                        return AnimatedContainer(
                          duration: const Duration(milliseconds: 520),
                          curve: Curves.easeInOutCubic,
                          width: width,
                          height: double.infinity,
                          child: _NavButton(
                            item: _items[index],
                            selected: selected,
                            onTap: () => onSelected(index),
                          ),
                        );
                      }),
                    ),
                  ],
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}

class _NavButton extends StatelessWidget {
  const _NavButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final _NavItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final Color color = selected ? AppColors.white : AppColors.muted;
    return Semantics(
      selected: selected,
      button: true,
      label: item.label,
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(999),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 520),
            curve: Curves.easeInOutCubic,
            alignment: Alignment.center,
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 420),
              switchInCurve: Curves.easeOutCubic,
              switchOutCurve: Curves.easeInCubic,
              transitionBuilder: (child, animation) => FadeTransition(
                opacity: animation,
                child: ScaleTransition(
                  scale:
                      Tween<double>(begin: 0.94, end: 1.0).animate(animation),
                  child: child,
                ),
              ),
              child: selected
                  ? Row(
                      key: ValueKey<String>('sel-${item.label}'),
                      mainAxisAlignment: MainAxisAlignment.center,
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        AnimatedPwonyNavIcon(
                          kind: item.icon,
                          selected: true,
                          color: color,
                          size: 28,
                        ),
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(
                            item.label,
                            maxLines: 1,
                            overflow: TextOverflow.fade,
                            softWrap: false,
                            style: const TextStyle(
                              color: AppColors.white,
                              fontSize: 16,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    )
                  : Center(
                      key: ValueKey<String>('unsel-${item.label}'),
                      child: AnimatedPwonyNavIcon(
                        kind: item.icon,
                        selected: false,
                        color: color,
                        size: 30,
                      ),
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

class _NavItem {
  const _NavItem(this.label, this.icon);

  final String label;
  final PwonyIconKind icon;
}

// ── Circular feature menu button ────────────────────────────────────────────
class _CircularHamburgerButton extends StatefulWidget {
  const _CircularHamburgerButton({
    required this.isMenuOpen,
    required this.onPressed,
    this.size = 72,
  });

  final bool isMenuOpen;
  final VoidCallback onPressed;
  final double size;

  @override
  State<_CircularHamburgerButton> createState() =>
      _CircularHamburgerButtonState();
}

class _CircularHamburgerButtonState extends State<_CircularHamburgerButton>
    with SingleTickerProviderStateMixin {
  static const Duration _duration = Duration(milliseconds: 900);

  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: _duration,
    value: widget.isMenuOpen ? 1.0 : 0.0,
  );

  @override
  void didUpdateWidget(covariant _CircularHamburgerButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isMenuOpen != oldWidget.isMenuOpen) {
      // Slow, deliberate morph between hamburger and close icon.
      if (widget.isMenuOpen) {
        _controller.forward();
      } else {
        _controller.reverse();
      }
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final double size = widget.size;
    return GestureDetector(
      onTap: widget.onPressed,
      behavior: HitTestBehavior.opaque,
      child: Semantics(
        button: true,
        label: 'Toggle features',
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, child) {
            final double t =
                Curves.easeInOutCubic.transform(_controller.value);
            // Gentle breathing pulse while transitioning so the motion is
            // obvious; settles back to resting size at both ends.
            final double pulse = 1.0 + 0.18 * (4 * t * (1 - t));
            return Transform.scale(
              scale: pulse,
              child: Transform.rotate(
                // Subtle half-turn flourish during the morph.
                angle: t * 0.5,
                child: Container(
                  width: size,
                  height: size,
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(
                      alpha: 0.6 + 0.25 * t,
                    ),
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: AppColors.blue.withValues(alpha: 0.35 + 0.45 * t),
                      width: 1.5 + 1.0 * t,
                    ),
                    boxShadow: <BoxShadow>[
                      BoxShadow(
                        color: AppColors.blue.withValues(alpha: 0.15 + 0.35 * t),
                        blurRadius: 12 + 18 * t,
                        offset: const Offset(0, 4),
                      ),
                    ],
                  ),
                  child: CustomPaint(
                    painter: _HamburgerPainter(
                      progress: t,
                      color: AppColors.white,
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _HamburgerPainter extends CustomPainter {
  const _HamburgerPainter({
    required this.progress,
    required this.color,
  });

  /// 0.0 = hamburger, 1.0 = close (X). Interpolated for a smooth morph.
  final double progress;
  final Color color;

  static Offset _lerp(Offset a, Offset b, double t) =>
      Offset(a.dx + (b.dx - a.dx) * t, a.dy + (b.dy - a.dy) * t);

  @override
  void paint(Canvas canvas, Size size) {
    final double t = progress;
    final Paint paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = size.width * 0.022
      ..strokeCap = StrokeCap.round;

    // Top line morphs into the \"\" diagonal of the X.
    canvas.drawLine(
      _lerp(Offset(size.width * 0.3, size.height * 0.36),
          Offset(size.width * 0.35, size.height * 0.35), t),
      _lerp(Offset(size.width * 0.7, size.height * 0.36),
          Offset(size.width * 0.65, size.height * 0.65), t),
      paint,
    );

    // Middle line fades out and shrinks toward center.
    final double midAlpha = (1.0 - t).clamp(0.0, 1.0);
    if (midAlpha > 0.01) {
      final Paint midPaint = Paint()
        ..color = color.withValues(alpha: midAlpha)
        ..style = PaintingStyle.stroke
        ..strokeWidth = size.width * 0.022
        ..strokeCap = StrokeCap.round;
      final double shrink = 0.2 * t;
      canvas.drawLine(
        Offset(size.width * (0.3 + shrink), size.height * 0.5),
        Offset(size.width * (0.7 - shrink), size.height * 0.5),
        midPaint,
      );
    }

    // Bottom line morphs into the "/" diagonal of the X.
    canvas.drawLine(
      _lerp(Offset(size.width * 0.3, size.height * 0.64),
          Offset(size.width * 0.65, size.height * 0.35), t),
      _lerp(Offset(size.width * 0.7, size.height * 0.64),
          Offset(size.width * 0.35, size.height * 0.65), t),
      paint,
    );
  }

  @override
  bool shouldRepaint(covariant _HamburgerPainter oldDelegate) {
    return oldDelegate.progress != progress || oldDelegate.color != color;
  }
}

// ── Floating feature menu panel ─────────────────────────────────────────────
class _FeatureMenu extends StatelessWidget {
  const _FeatureMenu({required this.onItemSelected, this.animateIn = true});

  final ValueChanged<int> onItemSelected;
  final bool animateIn;

  @override
  Widget build(BuildContext context) {
    final double viewportHeight = MediaQuery.sizeOf(context).height;
    final double maxHeight =
        viewportHeight < 420 ? 260 : (viewportHeight > 600 ? 400 : viewportHeight * 0.62);

    final double menuWidth =
        MediaQuery.sizeOf(context).width < 400 ? 270 : 310;
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: Container(
          width: menuWidth,
          constraints: BoxConstraints(maxHeight: maxHeight),
          decoration: BoxDecoration(
            color: AppColors.surface.withValues(alpha: 0.92),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: AppColors.border.withValues(alpha: 0.55),
            ),
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.42),
                blurRadius: 20,
                offset: const Offset(0, 10),
              ),
            ],
          ),
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              const Text(
                'Features',
                style: TextStyle(
                  color: AppColors.white,
                  fontSize: 22,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 16),
              _StaggeredMenuItem(
                index: 0,
                animateIn: animateIn,
                label: 'Flashcards',
                icon: PwonyIconKind.flashcard,
                onPressed: () => onItemSelected(0),
              ),
              _StaggeredMenuItem(
                index: 1,
                animateIn: animateIn,
                label: 'Discover Videos',
                icon: PwonyIconKind.play,
                onPressed: () => onItemSelected(1),
              ),
              _StaggeredMenuItem(
                index: 2,
                animateIn: animateIn,
                label: 'Test Yourself',
                icon: PwonyIconKind.startLearning,
                onPressed: () => onItemSelected(2),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StaggeredMenuItem extends StatelessWidget {
  const _StaggeredMenuItem({
    required this.index,
    required this.animateIn,
    required this.label,
    required this.icon,
    required this.onPressed,
  });

  final int index;
  final bool animateIn;
  final String label;
  final PwonyIconKind icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween<double>(begin: 0.0, end: animateIn ? 1.0 : 0.0),
      duration: const Duration(milliseconds: 700),
      curve: Interval(
        0.15 + index * 0.18,
        1.0,
        curve: Curves.easeOutCubic,
      ),
      builder: (context, value, child) {
        return Opacity(
          opacity: value,
          child: Transform.translate(
            offset: Offset(0, 24 * (1 - value)),
            child: child,
          ),
        );
      },
      child: _FeatureMenuItem(
        label: label,
        icon: icon,
        onPressed: onPressed,
      ),
    );
  }
}

class _FeatureMenuItem extends StatelessWidget {
  const _FeatureMenuItem({
    required this.label,
    required this.icon,
    required this.onPressed,
  });

  final String label;
  final PwonyIconKind icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          onTap: onPressed,
          borderRadius: BorderRadius.circular(14),
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: 12,
              vertical: 16,
            ),
            child: Row(
              children: <Widget>[
                PwonyIcon(icon, size: 24, color: AppColors.muted),
                const SizedBox(width: 14),
                Flexible(
                  child: Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AppColors.white,
                      fontSize: 17,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
