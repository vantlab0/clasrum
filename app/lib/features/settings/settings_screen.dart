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

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  Map<String, dynamic>? _student;
  bool _loading = true;
  String? _error;
  String? _message;
  bool _sendingReset = false;

  late String _level;
  late int _duration;

  @override
  void initState() {
    super.initState();
    _level = widget.storage.defaultLearningLevel ?? 'beginner';
    _duration = widget.storage.defaultDurationMinutes ?? 30;
    _load();
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

  Future<void> _load() async {
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
          final remoteLevel = decoded['default_level'] as String?;
          final remoteDuration = decoded['default_duration_minutes'] as int?;
          setState(() {
            _student = decoded;
            _level = remoteLevel ?? _level;
            _duration = remoteDuration ?? _duration;
            _loading = false;
          });
        } else {
          setState(() {
            _loading = false;
            _error = 'Unable to load account details.';
          });
        }
      } else if (response.statusCode == 401 || response.statusCode == 403) {
        await _handleAuthFailure();
      } else {
        setState(() {
          _loading = false;
          _error = 'Unable to load account details.';
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

  Future<void> _savePreferences() async {
    await widget.storage.setDefaultLearningLevel(_level);
    await widget.storage.setDefaultDurationMinutes(_duration);
    if (!mounted) return;
    setState(() => _message = 'Preferences saved on this device.');
  }

  Future<void> _sendPasswordReset() async {
    if (_sendingReset) return;
    setState(() {
      _sendingReset = true;
      _error = null;
      _message = null;
    });
    try {
      final auth = AuthScope.maybeOf(context)?.authService;
      final email = auth?.currentUser?.email ?? _student?['email'] as String?;
      if (auth == null || email == null || email.trim().isEmpty) {
        setState(() => _error = 'No account email available for password reset.');
      } else {
        await auth.resetPassword(email: email);
        if (mounted) {
          setState(() => _message = 'Password reset email sent to $email.');
        }
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyErrorMessage(e));
    } finally {
      if (mounted) setState(() => _sendingReset = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        leading: IconButton(
          onPressed: () => Navigator.of(context).maybePop(),
          icon: const ClasrumIcon(ClasrumIconKind.back, size: 20),
          tooltip: 'Back',
        ),
        title: const Text('Settings'),
      ),
      body: SafeArea(
        child: _loading
            ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
            : ListView(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
                children: <Widget>[
                  if (_message != null) ...[
                    GlassContainer(
                      radius: 14,
                      padding: const EdgeInsets.all(12),
                      backgroundOpacity: 0.35,
                      blurSigma: 8,
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          const ClasrumIcon(ClasrumIconKind.info,
                              size: 16, color: AppColors.blue),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _message!,
                              style: const TextStyle(
                                  color: AppColors.white, fontSize: 13),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                  ],
                  if (_error != null) ...[
                    Text(_error!,
                        style: const TextStyle(
                            color: AppColors.red, fontSize: 13)),
                    const SizedBox(height: 12),
                  ],
                  _section(
                    title: 'Account',
                    children: <Widget>[
                      _infoRow(
                        'Name',
                        (_student?['display_name'] as String?)?.trim().isNotEmpty == true
                            ? _student!['display_name'] as String
                            : '—',
                      ),
                      _divider(),
                      _infoRow('Email', _student?['email'] as String? ?? '—'),
                      _divider(),
                      _actionRow(
                        icon: ClasrumIconKind.passwordSecurity,
                        label: _sendingReset
                            ? 'Sending reset email…'
                            : 'Password & security',
                        subtitle: 'Send a password reset email to your account.',
                        onTap: _sendingReset ? null : _sendPasswordReset,
                        trailing: _sendingReset
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const ClasrumIcon(ClasrumIconKind.chevronRight,
                                size: 18, color: AppColors.muted),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _section(
                    title: 'Preferences',
                    children: <Widget>[
                      _dropdownRow<String>(
                        label: 'Default level',
                        value: _level,
                        items: learningLevels,
                        labelOf: levelLabel,
                        onChanged: (v) {
                          if (v != null) setState(() => _level = v);
                        },
                      ),
                      _divider(),
                      _dropdownRow<int>(
                        label: 'Default duration',
                        value: _duration,
                        items: supportedDurations,
                        labelOf: durationLabel,
                        onChanged: (v) {
                          if (v != null) setState(() => _duration = v);
                        },
                      ),
                      _divider(),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
                        child: Align(
                          alignment: Alignment.centerRight,
                          child: PrimaryActionButton(
                            compact: true,
                            onPressed: _savePreferences,
                            label: 'Save preferences',
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 4),
                    child: Text(
                      'Only settings supported by the current app are shown.',
                      style: TextStyle(color: AppColors.muted, fontSize: 12),
                    ),
                  ),
                ],
              ),
      ),
    );
  }

  Widget _section({required String title, required List<Widget> children}) {
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

  Widget _infoRow(String label, String value) => Padding(
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

  Widget _dropdownRow<T>({
    required String label,
    required T value,
    required List<T> items,
    required String Function(T) labelOf,
    required ValueChanged<T?> onChanged,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(label,
                style: const TextStyle(color: AppColors.muted, fontSize: 14)),
          ),
          DropdownButtonHideUnderline(
            child: DropdownButton<T>(
              value: value,
              dropdownColor: AppColors.surface,
              icon: const ClasrumIcon(ClasrumIconKind.chevronDown,
                  size: 16, color: AppColors.muted),
              style: const TextStyle(
                color: AppColors.white,
                fontSize: 14,
                fontWeight: FontWeight.w500,
              ),
              items: items
                  .map((e) => DropdownMenuItem<T>(
                        value: e,
                        child: Text(labelOf(e)),
                      ))
                  .toList(),
              onChanged: onChanged,
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
    Widget? trailing,
  }) {
    final enabled = onTap != null;
    return Material(
      type: MaterialType.transparency,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              ClasrumIcon(icon, size: 20, color: AppColors.muted),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      label,
                      style: TextStyle(
                        color: enabled ? AppColors.white : AppColors.muted,
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    if (subtitle != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        subtitle,
                        style: const TextStyle(
                          color: AppColors.muted,
                          fontSize: 12,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (trailing != null) ...[
                const SizedBox(width: 8),
                trailing,
              ],
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