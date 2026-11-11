import 'dart:convert' as convert;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/errors.dart';
import '../../core/storage.dart';

/// Profile screen: settings + data-deletion control (CONTRACTS §5 / §7).
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

  Future<void> _loadMe() async {
    try {
      final token = widget.storage.token;
      if (token == null) return;
      final response = await http.get(
        widget.config.apiUri('/me'),
        headers: <String, String>{'Authorization': 'Bearer $token'},
      );
      if (response.statusCode == 200) {
        final data = convert.jsonDecode(response.body) as Map<String, dynamic>;
        if (mounted) {
          setState(() {
            _student = data;
            _loading = false;
          });
        }
      } else if (response.statusCode == 401) {
        if (mounted) setState(() => _loading = false);
      }
    } catch (e) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _deleteMyData() async {
    final confirmed = await showDialog<bool>(
          context: context,
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
                style: FilledButton.styleFrom(backgroundColor: const Color(0xFFE39C9C)),
                child: const Text('Delete everything'),
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
        headers: <String, String>{
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
        body: convert.jsonEncode(<String, dynamic>{'confirm': true}),
      );
      if (response.statusCode == 200) {
        await widget.storage.clearAccountScopedData();
        if (mounted) {
          Navigator.pop(context, true); // signal sign-out
        }
      } else {
        final exc = ApiException.fromEnvelope(
          statusCode: response.statusCode,
          body: convert.jsonDecode(response.body) as Map<String, dynamic>,
        );
        setState(() => _error = exc.friendlyMessage);
      }
    } catch (e) {
      setState(() => _error = friendlyErrorMessage(e));
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _student == null
              ? Center(
                  child: Text(_error ?? 'Unable to load profile.'))
              : ListView(
                  padding: const EdgeInsets.all(24),
                  children: <Widget>[
                    const Text('Account',
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 12),
                    ListTile(
                      leading: const Icon(Icons.person),
                      title: Text(_student!['display_name'] as String? ?? 'Student'),
                      subtitle: Text(_student!['email'] as String? ?? ''),
                    ),
                    const SizedBox(height: 16),
                    const Text('Defaults',
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 12),
                    ListTile(
                      title: const Text('Default level'),
                      subtitle: Text(_student!['default_level'] as String? ?? 'beginner'),
                    ),
                    ListTile(
                      title: const Text('Default duration'),
                      subtitle: Text('${_student!['default_duration_minutes'] as int? ?? 20} min'),
                    ),
                    const SizedBox(height: 32),
                    const Text('Danger Zone',
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: Color(0xFFE39C9C))),
                    const SizedBox(height: 12),
                    Card(
                      color: const Color(0xFF2A1515),
                      child: ListTile(
                        leading: const Icon(Icons.delete_forever, color: Color(0xFFE39C9C)),
                        title: const Text('Delete all my data'),
                        subtitle: const Text(
                          'Permanently removes lessons, transcripts, board history, notes and memory. Cannot be undone.',
                        ),
                        onTap: _deleting ? null : _deleteMyData,
                        trailing: _deleting
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.chevron_right),
                      ),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.all(12),
                        color: const Color(0xFF2A1515),
                        child: Text(_error!,
                            style: const TextStyle(color: Color(0xFFE39C9C))),
                      ),
                    ],
                  ],
                ),
    );
  }
}
