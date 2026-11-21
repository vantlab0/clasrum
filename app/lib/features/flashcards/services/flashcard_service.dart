import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../../core/config.dart';
import '../../../core/storage.dart';
import '../models/flashcard.dart';

/// Thrown for predictable flashcard API failures so the UI can show a clear
/// message instead of a raw HTTP response.
class FlashcardServiceException implements Exception {
  const FlashcardServiceException(this.message, {this.unauthorized = false});

  final String message;
  final bool unauthorized;

  @override
  String toString() => message;
}

/// Thin client around the existing lesson-scoped flashcard endpoints.
///
/// Generation itself stays in the backend generator; this service only starts
/// a run, polls the persisted set until it is ready, and parses the result.
class FlashcardService {
  FlashcardService({
    required this.config,
    required this.storage,
    http.Client? httpClient,
  }) : _http = httpClient ?? http.Client();

  final AppConfig config;
  final AppStorage storage;
  final http.Client _http;

  static const Duration _defaultPollInterval = Duration(milliseconds: 900);
  static const Duration _defaultTimeout = Duration(seconds: 90);

  Map<String, String> get _headers {
    final token = storage.token;
    return <String, String>{
      'Content-Type': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
  }

  /// Creates a lesson so a topic-only entry point can still use the
  /// lesson-scoped generator.
  Future<String> createLesson({
    required String topic,
    required String learningLevel,
    required int durationMinutes,
    String? subject,
  }) async {
    final response = await _http.post(
      config.apiUri('/lessons'),
      headers: _headers,
      body: jsonEncode(<String, dynamic>{
        'topic': topic,
        'learning_level': learningLevel,
        'duration_minutes': durationMinutes,
        if (subject != null && subject.trim().isNotEmpty)
          'subject': subject.trim(),
      }),
    );
    final body = _decodeObject(response);
    if (response.statusCode != 200) {
      throw _errorFor(response, body);
    }
    final id = body['id'] as String?;
    if (id == null || id.isEmpty) {
      throw const FlashcardServiceException(
          'The server did not return a lesson ID.');
    }
    return id;
  }

  /// Starts generation for a lesson and returns the pending/ready summary.
  Future<FlashcardSetSummary> generate({
    required String lessonId,
    required String topic,
    String? subject,
    String? classLevel,
  }) async {
    final response = await _http.post(
      config.apiUri('/lessons/$lessonId/flashcards/generate'),
      headers: _headers,
      body: jsonEncode(<String, dynamic>{
        'topic': topic,
        if (subject != null && subject.trim().isNotEmpty)
          'subject': subject.trim(),
        if (classLevel != null && classLevel.trim().isNotEmpty)
          'class_level': classLevel.trim(),
      }),
    );
    final body = _decodeObject(response);
    if (response.statusCode != 200) {
      throw _errorFor(response, body);
    }
    return FlashcardSetSummary.fromJson(body);
  }

  /// Fetches the persisted set, including all generated cards.
  Future<FlashcardSet> fetchSet({
    required String lessonId,
    required String setId,
  }) async {
    final response = await _http.get(
      config.apiUri('/lessons/$lessonId/flashcards/$setId'),
      headers: _headers,
    );
    final body = _decodeObject(response);
    if (response.statusCode != 200) {
      throw _errorFor(response, body);
    }
    return FlashcardSet.fromJson(body);
  }

  /// Lists previously generated sets for a lesson (most recent first).
  Future<List<FlashcardSetSummary>> listForLesson(String lessonId) async {
    final response = await _http.get(
      config.apiUri('/lessons/$lessonId/flashcards'),
      headers: _headers,
    );
    final body = _decodeObject(response);
    if (response.statusCode != 200) {
      throw _errorFor(response, body);
    }
    final rawItems = body['items'];
    if (rawItems is! List) return const <FlashcardSetSummary>[];
    return rawItems
        .whereType<Map<String, dynamic>>()
        .map(FlashcardSetSummary.fromJson)
        .toList(growable: false);
  }

  /// Polls until the background generator has persisted its cards.
  Future<FlashcardSet> waitUntilReady({
    required String lessonId,
    required String setId,
    Duration pollInterval = _defaultPollInterval,
    Duration timeout = _defaultTimeout,
  }) async {
    final deadline = DateTime.now().add(timeout);
    FlashcardSet? latest;

    while (DateTime.now().isBefore(deadline)) {
      latest = await fetchSet(lessonId: lessonId, setId: setId);
      if (latest.isFailed) {
        throw FlashcardServiceException(
          latest.error?.trim().isNotEmpty == true
              ? latest.error!.trim()
              : 'Flashcard generation failed. Please try again.',
        );
      }
      if (latest.isReady) return latest;
      await Future<void>.delayed(pollInterval);
    }

    if (latest != null && latest.cards.isNotEmpty) return latest;
    throw const FlashcardServiceException(
      'Flashcards are taking longer than expected. Please try again.',
    );
  }

  void close() {
    _http.close();
  }

  Map<String, dynamic> _decodeObject(http.Response response) {
    if (response.bodyBytes.isEmpty) return <String, dynamic>{};
    try {
      final decoded = jsonDecode(utf8.decode(response.bodyBytes));
      if (decoded is Map<String, dynamic>) return decoded;
      return <String, dynamic>{};
    } catch (_) {
      return <String, dynamic>{};
    }
  }

  FlashcardServiceException _errorFor(
    http.Response response,
    Map<String, dynamic> body,
  ) {
    if (response.statusCode == 401 || response.statusCode == 403) {
      return const FlashcardServiceException(
        'Your session has expired. Please sign in again.',
        unauthorized: true,
      );
    }

    final error = body['error'];
    if (error is Map<String, dynamic>) {
      final message = error['message'];
      if (message is String && message.trim().isNotEmpty) {
        return FlashcardServiceException(message.trim());
      }
    }
    final message = body['detail'] ?? body['message'];
    if (message is String && message.trim().isNotEmpty) {
      return FlashcardServiceException(message.trim());
    }
    return FlashcardServiceException(
      'Could not complete the flashcards request (HTTP ${response.statusCode}).',
    );
  }
}
