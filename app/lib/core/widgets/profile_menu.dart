import 'dart:ui';

import 'package:flutter/material.dart';

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../features/settings/settings_screen.dart';
import '../../features/upgrade/upgrade_screen.dart';
import '../../main.dart';
import '../../services/realtime_client.dart';
import 'clasrum_icons.dart';

/// Small circular profile control that opens an anchored glass account menu.
class ProfileMenuButton extends StatefulWidget {
  const ProfileMenuButton({
    super.key,
    required this.config,
    required this.storage,
    this.size = 36,
    this.avatarUrl,
  });

  final AppConfig config;
  final AppStorage storage;
  final double size;

  /// Optional avatar image URL. If null/empty/unavailable, a custom profile
  /// icon is shown.
  final String? avatarUrl;

  @override
  State<ProfileMenuButton> createState() => _ProfileMenuButtonState();
}

class _ProfileMenuButtonState extends State<ProfileMenuButton>
    with SingleTickerProviderStateMixin {
  final LayerLink _link = LayerLink();
  final GlobalKey _targetKey = GlobalKey();
  OverlayEntry? _entry;
  late final AnimationController _controller;
  late final Animation<double> _fade;
  late final Animation<Offset> _slide;
  late final Animation<double> _scale;
  bool _opening = false;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 220),
      reverseDuration: const Duration(milliseconds: 170),
    );
    final curved = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );
    _fade = curved;
    _slide = Tween<Offset>(begin: const Offset(0, -0.04), end: Offset.zero)
        .animate(curved);
    _scale = Tween<double>(begin: 0.96, end: 1.0).animate(curved);
  }

  @override
  void dispose() {
    _removeEntry(immediate: true);
    _controller.dispose();
    super.dispose();
  }

  void _toggleMenu() {
    if (_entry != null || _opening) {
      _closeMenu();
    } else {
      _openMenu();
    }
  }

  Future<void> _openMenu() async {
    if (!mounted || _entry != null || _opening) return;
    _opening = true;
    final overlay = Overlay.of(context, rootOverlay: true);
    final renderBox = _targetKey.currentContext?.findRenderObject() as RenderBox?;
    final overlayBox = overlay.context.findRenderObject() as RenderBox?;
    if (renderBox == null || overlayBox == null || !renderBox.hasSize) {
      _opening = false;
      return;
    }

    final targetOffset = renderBox.localToGlobal(Offset.zero, ancestor: overlayBox);
    final targetSize = renderBox.size;
    final screenSize = overlayBox.size;
    const menuWidth = 228.0;
    const edge = 12.0;
    final top = (targetOffset.dy + targetSize.height + 8).clamp(
      edge,
      screenSize.height - edge - 8,
    );
    final desiredLeft = targetOffset.dx + targetSize.width - menuWidth;
    final left = desiredLeft.clamp(edge, screenSize.width - menuWidth - edge);

    _entry = OverlayEntry(
      builder: (context) {
        return Stack(
          children: <Widget>[
            Positioned.fill(
              child: GestureDetector(
                behavior: HitTestBehavior.translucent,
                onTap: _closeMenu,
                child: const SizedBox.expand(),
              ),
            ),
            Positioned(
              left: left,
              top: top,
              width: menuWidth,
              child: FadeTransition(
                opacity: _fade,
                child: SlideTransition(
                  position: _slide,
                  child: ScaleTransition(
                    scale: _scale,
                    alignment: Alignment.topRight,
                    child: Material(
                      type: MaterialType.transparency,
                      child: _AccountDropdownMenu(
                        onSettings: () => _handleSelection(_openSettings),
                        onUpgrade: () => _handleSelection(_openUpgrade),
                        onLogout: () => _handleSelection(_logout),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );

    overlay.insert(_entry!);
    _opening = false;
    await _controller.forward(from: 0);
  }

  Future<void> _handleSelection(Future<void> Function() action) async {
    await _closeMenu();
    if (!mounted) return;
    await action();
  }

  Future<void> _closeMenu() async {
    if (_entry == null) return;
    try {
      if (_controller.status != AnimationStatus.dismissed) {
        await _controller.reverse();
      }
    } catch (_) {}
    _removeEntry(immediate: true);
  }

  void _removeEntry({bool immediate = false}) {
    _entry?.remove();
    _entry = null;
    if (immediate) {
      _controller.reset();
    }
  }

  Future<void> _openSettings() async {
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => SettingsScreen(
          config: widget.config,
          storage: widget.storage,
        ),
      ),
    );
  }

  Future<void> _openUpgrade() async {
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => const UpgradeScreen(),
      ),
    );
  }

  Future<void> _logout() async {
    if (!mounted) return;
    final confirmed = await showDialog<bool>(
          context: context,
          barrierColor: Colors.black.withValues(alpha: 0.55),
          builder: (dialogContext) => Dialog(
            backgroundColor: Colors.transparent,
            elevation: 0,
            insetPadding:
                const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(20),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
                child: Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: AppColors.surface.withValues(alpha: 0.78),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(
                      color: AppColors.border.withValues(alpha: 0.55),
                    ),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      const Text(
                        'Log out?',
                        style: TextStyle(
                          color: AppColors.white,
                          fontSize: 17,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'You will be signed out of this device.',
                        style: TextStyle(
                          color: AppColors.muted,
                          fontSize: 14,
                          height: 1.45,
                        ),
                      ),
                      const SizedBox(height: 16),
                      Row(
                        children: <Widget>[
                          Expanded(
                            child: OutlinedButton(
                              onPressed: () => Navigator.of(dialogContext).pop(false),
                              style: OutlinedButton.styleFrom(
                                foregroundColor: AppColors.white,
                                side: BorderSide(
                                  color: AppColors.border.withValues(alpha: 0.7),
                                ),
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(12),
                                ),
                              ),
                              child: const Text('Cancel'),
                            ),
                          ),
                          const SizedBox(width: 10),
                          Expanded(
                            child: FilledButton(
                              onPressed: () => Navigator.of(dialogContext).pop(true),
                              style: FilledButton.styleFrom(
                                backgroundColor: AppColors.blue,
                                foregroundColor: AppColors.white,
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(12),
                                ),
                              ),
                              child: const Text('Log out'),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ) ??
        false;
    if (!confirmed || !mounted) return;

    try {
      final realtime = RealtimeClientScope.maybeOf(context);
      if (realtime != null) {
        await realtime.disconnect();
      }
    } catch (_) {}
    if (!mounted) return;
    final auth = AuthScope.maybeOf(context)?.authService;
    if (auth != null) {
      await auth.signOut();
    } else {
      await widget.storage.clearUserScopedData();
    }
    // AuthGate reacts to auth state changes and returns to authentication.
  }

  @override
  Widget build(BuildContext context) {
    final avatarUrl = widget.avatarUrl?.trim();
    final hasAvatar = avatarUrl != null && avatarUrl.isNotEmpty;
    final button = Semantics(
      button: true,
      label: 'Account menu',
      child: CompositedTransformTarget(
        link: _link,
        child: SizedBox(
          key: _targetKey,
          width: widget.size,
          height: widget.size,
          child: ClipOval(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
              child: Material(
                type: MaterialType.transparency,
                child: InkWell(
                  onTap: _toggleMenu,
                  customBorder: const CircleBorder(),
                  child: Ink(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: AppColors.surface.withValues(alpha: 0.55),
                      border: Border.all(
                        color: AppColors.border.withValues(alpha: 0.55),
                      ),
                    ),
                    child: Center(
                      child: hasAvatar
                          ? ClipOval(
                              child: Image.network(
                                avatarUrl,
                                width: widget.size,
                                height: widget.size,
                                fit: BoxFit.cover,
                                errorBuilder: (_, __, ___) => ClasrumIcon(
                                  ClasrumIconKind.profile,
                                  size: widget.size * 0.5,
                                  color: AppColors.muted,
                                ),
                              ),
                            )
                          : ClasrumIcon(
                              ClasrumIconKind.profile,
                              size: widget.size * 0.5,
                              color: AppColors.muted,
                            ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
    return button;
  }
}

class _AccountDropdownMenu extends StatelessWidget {
  const _AccountDropdownMenu({
    required this.onSettings,
    required this.onUpgrade,
    required this.onLogout,
  });

  final VoidCallback onSettings;
  final VoidCallback onUpgrade;
  final VoidCallback onLogout;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
        child: Container(
          decoration: BoxDecoration(
            color: AppColors.surface.withValues(alpha: 0.72),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: AppColors.border.withValues(alpha: 0.55)),
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.35),
                blurRadius: 24,
                offset: const Offset(0, 10),
              ),
            ],
          ),
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              _MenuItem(
                icon: ClasrumIconKind.settings,
                label: 'Settings',
                onTap: onSettings,
              ),
              _MenuItem(
                icon: ClasrumIconKind.upgradePro,
                label: 'Upgrade to Pro',
                onTap: onUpgrade,
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                child: Divider(
                  height: 1,
                  thickness: 1,
                  color: AppColors.border.withValues(alpha: 0.35),
                ),
              ),
              _MenuItem(
                icon: ClasrumIconKind.logout,
                label: 'Logout',
                onTap: onLogout,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MenuItem extends StatefulWidget {
  const _MenuItem({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final ClasrumIconKind icon;
  final String label;
  final VoidCallback onTap;

  @override
  State<_MenuItem> createState() => _MenuItemState();
}

class _MenuItemState extends State<_MenuItem> {
  bool _hovered = false;
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final active = _hovered || _pressed;
    final bg = active
        ? AppColors.white.withValues(alpha: 0.06)
        : Colors.transparent;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      child: MouseRegion(
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTapDown: (_) => setState(() => _pressed = true),
          onTapCancel: () => setState(() => _pressed = false),
          onTapUp: (_) => setState(() => _pressed = false),
          onTap: widget.onTap,
          behavior: HitTestBehavior.opaque,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 140),
            curve: Curves.easeOutCubic,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              children: <Widget>[
                ClasrumIcon(widget.icon, size: 18, color: AppColors.white),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    widget.label,
                    style: const TextStyle(
                      color: AppColors.white,
                      fontSize: 14,
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