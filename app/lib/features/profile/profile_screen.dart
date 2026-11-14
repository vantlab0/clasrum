import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/formatters.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../main.dart';
import '../../services/realtime_client.dart';

/// Profile screen: account, supported settings, and data controls.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  Map<String, dynamic>? _student;
  bool _loading = true;
  String? _error;
  bool _deleting = false;

  @override
  void initState() {
    super.initState();
    _loadMe();
  }

  Map<String, String> get _authHeaders {
    final token = widget.storage.token;
    return <String, String>{
      if (token != null) 'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Future<void> _handleAuthFailure() async {
    try {
      final realtime = RealtimeClientScope.maybeOf(context);
      if (realtime != null) await realtime.disconnect();
    } catch (_) {}
    if (!mounted) return;
    final auth = AuthScope.maybeOf(context)?.authService;
    if (auth != null) await auth.signOutExpiredSession();
  }

  Future<void> _loadMe() async {
    try {
      final token = widget.storage.token;
      if (token == null) {
        if (mounted) setState(() => _loading = false);
        return;
      }
      final response = await http.get(
        widget.config.apiUri('/me'),
        headers: _authHeaders,
      );
      if (!mounted) return;
      if (response.statusCode == 200) {
        final decoded = convert.jsonDecode(response.body);
        if (decoded is Map<String, dynamic>) {
          setState(() {
            _student = decoded;
            _error = null;
            _loading = false;
          });
        } else {
          setState(() {
            _loading = false;
            _error = 'Unable to load profile.';
          });
        }
      } else if (response.statusCode == 401 || response.statusCode == 403) {
        await _handleAuthFailure();
        if (mounted) {
          setState(() {
            _student = null;
            _loading = false;
            _error = 'Unable to load profile from the server. Please try again later.';
          });
        }
      } else {
        setState(() {
          _loading = false;
          _error = 'Unable to load profile.';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = friendlyErrorMessage(e);
        });
      }
    }
  }

  Future<void> _signOut() async {
    try {
      final realtime = RealtimeClientScope.maybeOf(context);
      if (realtime != null) await realtime.disconnect();
    } catch (_) {}
    if (!mounted) return;
    final auth = AuthScope.maybeOf(context)?.authService;
    if (auth != null) {
      await auth.signOut();
    } else {
      await widget.storage.clearUserScopedData();
    }
  }

  Future<void> _deleteMyData() async {
    final confirmed = await showDialog<bool>(
          context: context,
          barrierColor: Colors.black.withValues(alpha: 0.55),
          builder: (context) => AlertDialog(
            title: const Text('Delete all your data?'),
            content: const Text(
              'This permanently removes your lessons, transcripts, board history, '
              'notes and learning memory. It cannot be undone.',
            ),
            actions: <Widget>[
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.red,
                  foregroundColor: AppColors.white,
                ),
                child: const Text('Delete'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;

    setState(() {
      _deleting = true;
      _error = null;
    });
    try {
      final token = widget.storage.token;
      if (token == null) throw ApiException.unauthorized();
      final response = await http.delete(
        widget.config.apiUri('/me/data'),
        headers: _authHeaders,
        body: convert.jsonEncode(<String, dynamic>{'confirm': true}),
      );
      if (!mounted) return;
      if (response.statusCode == 200) {
        final messenger = ScaffoldMessenger.maybeOf(context);
        final realtime = RealtimeClientScope.maybeOf(context);
        final auth = AuthScope.maybeOf(context)?.authService;
        messenger?.showSnackBar(
          const SnackBar(content: Text('All data deleted.')),
        );
        try {
          if (realtime != null) await realtime.disconnect();
        } catch (_) {}
        if (auth != null) {
          await auth.signOut();
        } else {
          await widget.storage.clearUserScopedData();
        }
        if (mounted) {
          setState(() => _deleting = false);
        }
      } else {
        Map<String, dynamic> body = const <String, dynamic>{};
        try {
          final decoded = convert.jsonDecode(response.body);
          if (decoded is Map<String, dynamic>) body = decoded;
        } catch (_) {}
        final exc = ApiException.fromEnvelope(
          statusCode: response.statusCode,
          body: body,
        );
        if (exc.isAuthFailure) {
          await _handleAuthFailure();
          return;
        }
        setState(() {
          _error = exc.friendlyMessage;
          _deleting = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = friendlyErrorMessage(e);
          _deleting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = 110 + MediaQuery.paddingOf(context).bottom;
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        bottom: false,
        child: _loading
            ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
            : ListView(
                padding: EdgeInsets.fromLTRB(20, 16, 20, bottomPad),
                children: <Widget>[
                  _buildHeader(context),
                  const SizedBox(height: 24),
                  if (_student == null)
                    _signedOutState()
                  else ...[
                    _group(
                      title: 'Account',
                      children: <Widget>[
                        _infoRow('Name',
                            _student!['display_name'] as String? ?? '—'),
                        _divider(),
                        _infoRow(
                            'Email', _student!['email'] as String? ?? '—'),
                      ],
                    ),
                    const SizedBox(height: 14),
                    _group(
                      title: 'Learning defaults',
                      children: <Widget>[
                        _infoRow(
                          'Level',
                          levelLabel(
                              _student!['default_level'] as String? ??
                                  widget.storage.defaultLearningLevel ??
                                  'beginner'),
                        ),
                        _divider(),
                        _infoRow(
                          'Duration',
                          durationLabel(
                            (_student!['default_duration_minutes'] as int?) ??
                                widget.storage.defaultDurationMinutes ??
                                30,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 14),
                    _group(
                      title: 'Account actions',
                      children: <Widget>[
                        _actionRow(
                          icon: ClasrumIconKind.logout,
                          label: 'Sign out',
                          onTap: _signOut,
                        ),
                        _divider(),
                        _actionRow(
                          icon: ClasrumIconKind.delete,
                          label: _deleting ? 'Deleting data…' : 'Delete all my data',
                          subtitle:
                              'Removes lessons, transcripts, board history, notes and memory.',
                          onTap: _deleting ? null : _deleteMyData,
                          showChevron: !_deleting,
                          trailing: _deleting
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child:
                                      CircularProgressIndicator(strokeWidth: 2),
                                )
                              : null,
                        ),
                      ],
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!,
                        style: const TextStyle(
                            color: AppColors.red, fontSize: 13)),
                  ],
                ],
              ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    final name = _student?['display_name'] as String? ?? 'Profile';
    final email = _student?['email'] as String?;
    return Row(
      children: <Widget>[
        Container(
          width: 48,
          height: 48,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: AppColors.surface.withValues(alpha: 0.6),
            border: Border.all(color: AppColors.border.withValues(alpha: 0.6)),
          ),
          child: const ClasrumIcon(ClasrumIconKind.profile,
              color: AppColors.muted, size: 22),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                name,
                style: Theme.of(context)
                    .textTheme
                    .titleLarge
                    ?.copyWith(fontWeight: FontWeight.w600),
              ),
              if (email != null && email.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(email,
                    style: const TextStyle(
                        color: AppColors.muted, fontSize: 13)),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _signedOutState() {
    return GlassContainer(
      radius: 18,
      padding: const EdgeInsets.all(20),
      blurSigma: 8,
      backgroundOpacity: 0.3,
      child: const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Not signed in',
              style: TextStyle(
                  color: AppColors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.w600)),
          SizedBox(height: 6),
          Text(
            'Sign in to view your account details and learning data.',
            style: TextStyle(color: AppColors.muted, fontSize: 14, height: 1.5),
          ),
        ],
      ),
    );
  }

  Widget _group({required String title, required List<Widget> children}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Text(
            title,
            style: const TextStyle(
              color: AppColors.muted,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
        GlassContainer(
          radius: 18,
          padding: EdgeInsets.zero,
          blurSigma: 8,
          backgroundOpacity: 0.3,
          child: Column(children: children),
        ),
      ],
    );
  }

  Widget _infoRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(label,
                style:
                    const TextStyle(color: AppColors.muted, fontSize: 14)),
          ),
          const SizedBox(width: 12),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: const TextStyle(
                  color: AppColors.white,
                  fontSize: 14,
                  fontWeight: FontWeight.w500),
            ),
          ),
        ],
      ),
    );
  }

  Widget _actionRow({
    required ClasrumIconKind icon,
    required String label,
    String? subtitle,
    VoidCallback? onTap,
    bool showChevron = true,
    Widget? trailing,
  }) {
    final enabled = onTap != null;
    final color = enabled ? AppColors.white : AppColors.muted;
    return Material(
      type: MaterialType.transparency,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            children: <Widget>[
              ClasrumIcon(icon, size: 20, color: AppColors.muted),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(label,
                        style: TextStyle(
                            color: color,
                            fontSize: 14,
                            fontWeight: FontWeight.w500)),
                    if (subtitle != null) ...[
                      const SizedBox(height: 2),
                      Text(subtitle,
                          style: const TextStyle(
                              color: AppColors.muted,
                              fontSize: 12,
                              height: 1.4)),
                    ],
                  ],
                ),
              ),
              if (trailing != null)
                trailing
              else if (showChevron)
                const ClasrumIcon(ClasrumIconKind.chevronRight,
                    size: 18, color: AppColors.muted),
            ],
          ),
        ),
      ),
    );
  }

  Widget _divider() => Divider(
        height: 1,
        thickness: 1,
        indent: 16,
        endIndent: 16,
        color: AppColors.border.withValues(alpha: 0.35),
      );
}
