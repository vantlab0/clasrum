import 'dart:ui';

import 'package:flutter/material.dart';

import 'core/config.dart';
import 'core/storage.dart';
import 'core/theme.dart';
import 'core/widgets/clasrum_icons.dart';
import 'features/home/home_screen.dart';
import 'features/live/live_screen.dart';
import 'features/notes/notes_screen.dart';
import 'features/profile/profile_screen.dart';
import 'services/realtime_client.dart';

/// Shell with exactly three destinations: Home, Notes, Profile.
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
            ProfileScreen(config: widget.config, storage: widget.storage),
          ],
        ),
        bottomNavigationBar: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: Align(
              alignment: Alignment.bottomCenter,
              heightFactor: 1,
              child: _FloatingNavBar(
                selectedIndex: _index,
                onSelected: (i) => setState(() => _index = i),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _FloatingNavBar extends StatelessWidget {
  const _FloatingNavBar({
    required this.selectedIndex,
    required this.onSelected,
  });

  final int selectedIndex;
  final ValueChanged<int> onSelected;

  static const _items = <_NavItem>[
    _NavItem('Home', ClasrumIconKind.home),
    _NavItem('Notes', ClasrumIconKind.notes),
    _NavItem('Profile', ClasrumIconKind.profile),
  ];

  @override
  Widget build(BuildContext context) {
    // Fixed container width = 3 equal slots.
    const double slot = 72;
    const double containerWidth = slot * 3; // 216
    const double height = 60;
    const double pad = 6;

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
                final innerW = constraints.maxWidth; // 204
                final unit = innerW / 3;
                // Active = 1.5u, each inactive = 0.75u, total = 3u
                double leftFor(int i) {
                  double left = 0;
                  for (var j = 0; j < i; j++) {
                    left += j == selectedIndex ? 1.5 * unit : 0.75 * unit;
                  }
                  return left;
                }

                return Stack(
                  children: <Widget>[
                    // Moving active pill background
                    AnimatedPositioned(
                      duration: const Duration(milliseconds: 320),
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
                      children: List<Widget>.generate(3, (i) {
                        final selected = i == selectedIndex;
                        final width = selected ? 1.5 * unit : 0.75 * unit;
                        return AnimatedContainer(
                          duration: const Duration(milliseconds: 320),
                          curve: Curves.easeInOutCubic,
                          width: width,
                          height: double.infinity,
                          child: _NavButton(
                            item: _items[i],
                            selected: selected,
                            onTap: () => onSelected(i),
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
    final color = selected ? AppColors.white : AppColors.muted;
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
            duration: const Duration(milliseconds: 320),
            curve: Curves.easeInOutCubic,
            alignment: Alignment.center,
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 220),
              switchInCurve: Curves.easeOutCubic,
              switchOutCurve: Curves.easeInCubic,
              transitionBuilder: (child, animation) => FadeTransition(
                opacity: animation,
                child: ScaleTransition(
                  scale: Tween<double>(begin: 0.96, end: 1.0).animate(animation),
                  child: child,
                ),
              ),
              child: selected
                  ? Row(
                      key: ValueKey<String>('sel-${item.label}'),
                      mainAxisAlignment: MainAxisAlignment.center,
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        AnimatedClasrumNavIcon(
                          kind: item.icon,
                          selected: true,
                          color: color,
                          size: 20,
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            item.label,
                            maxLines: 1,
                            overflow: TextOverflow.fade,
                            softWrap: false,
                            style: const TextStyle(
                              color: AppColors.white,
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    )
                  : Center(
                      key: ValueKey<String>('unsel-${item.label}'),
                      child: AnimatedClasrumNavIcon(
                        kind: item.icon,
                        selected: false,
                        color: color,
                        size: 22,
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
  final ClasrumIconKind icon;
}
