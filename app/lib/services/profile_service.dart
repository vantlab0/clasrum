import 'package:supabase_flutter/supabase_flutter.dart';

import '../core/supabase_bootstrap.dart';

/// Repository/service for the authenticated user's Supabase profile row.
///
/// All access is scoped to the current authenticated user (`auth.uid()`).
/// Row Level Security must enforce per-user isolation on the `profiles` table.
class ProfileService {
  ProfileService({SupabaseClient? client}) : _clientOverride = client;

  final SupabaseClient? _clientOverride;

  SupabaseClient get _client {
    final client = _clientOverride ?? SupabaseBootstrap.clientOrNull;
    if (client == null) {
      throw const ProfileServiceException(
        'Supabase is not configured or failed to initialize.',
      );
    }
    return client;
  }

  static const String table = 'profiles';

  String _requireUserId() {
    final user = _client.auth.currentUser;
    final id = user?.id;
    if (id == null || id.isEmpty) {
      throw const ProfileServiceException('No authenticated user.');
    }
    return id;
  }

  /// Retrieves the current user's profile, or null if not found.
  Future<Map<String, dynamic>?> getCurrentProfile() async {
    final userId = _requireUserId();
    final data = await _client
        .from(table)
        .select()
        .eq('id', userId)
        .maybeSingle();
    if (data == null) return null;
    return Map<String, dynamic>.from(data);
  }

  /// Creates or updates the current user's profile.
  ///
  /// Only user-editable fields should be passed. The `id` is always taken from
  /// the authenticated session, never from client-supplied authorization input.
  Future<Map<String, dynamic>> upsertCurrentProfile({
    String? displayName,
    String? avatarUrl,
  }) async {
    final userId = _requireUserId();
    final now = DateTime.now().toUtc().toIso8601String();
    final payload = <String, dynamic>{
      'id': userId,
      if (displayName != null) 'display_name': displayName,
      'avatar_url': avatarUrl,
      'updated_at': now,
    };
    final data = await _client
        .from(table)
        .upsert(payload, onConflict: 'id')
        .select()
        .single();
    return Map<String, dynamic>.from(data);
  }

  /// Ensures a profile row exists for the current authenticated user.
  ///
  /// Useful after registration/sign-in if profile creation is not handled by a
  /// database trigger. Safe to call repeatedly.
  Future<Map<String, dynamic>> ensureCurrentProfile({
    String? displayName,
  }) async {
    final existing = await getCurrentProfile();
    if (existing != null) return existing;
    final user = _client.auth.currentUser;
    final fallbackName = displayName?.trim().isNotEmpty == true
        ? displayName!.trim()
        : (user?.userMetadata?['display_name'] as String?)?.trim();
    final now = DateTime.now().toUtc().toIso8601String();
    final payload = <String, dynamic>{
      'id': _requireUserId(),
      'display_name':
          (fallbackName != null && fallbackName.isNotEmpty) ? fallbackName : 'Student',
      'avatar_url': null,
      'created_at': now,
      'updated_at': now,
    };
    final data = await _client.from(table).insert(payload).select().single();
    return Map<String, dynamic>.from(data);
  }

  /// Updates only provided fields for the current user's profile.
  Future<Map<String, dynamic>> updateCurrentProfile({
    String? displayName,
    String? avatarUrl,
    bool clearAvatar = false,
  }) async {
    final userId = _requireUserId();
    final payload = <String, dynamic>{
      if (displayName != null) 'display_name': displayName,
      if (avatarUrl != null || clearAvatar) 'avatar_url': avatarUrl,
      'updated_at': DateTime.now().toUtc().toIso8601String(),
    };
    final data = await _client
        .from(table)
        .update(payload)
        .eq('id', userId)
        .select()
        .single();
    return Map<String, dynamic>.from(data);
  }
}

class ProfileServiceException implements Exception {
  const ProfileServiceException(this.message);
  final String message;

  @override
  String toString() => message;
}
