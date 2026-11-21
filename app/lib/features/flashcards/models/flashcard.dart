import 'package:flutter/foundation.dart';

/// One AI-generated flashcard.
///
/// The model is deliberately immutable: the flip state belongs to the study
/// screen, not to persisted card data. Card content comes from the existing
/// backend flashcard generator and is never hardcoded by the UI.
@immutable
class Flashcard {
  const Flashcard({
    required this.id,
    required this.question,
    required this.answer,
    this.explanation,
    this.order = 0,
    this.topic = '',
    this.subject,
    this.classLevel,
  });

  final String id;
  final String question;
  final String answer;
  final String? explanation;
  final int order;
  final String topic;
  final String? subject;
  final String? classLevel;

  factory Flashcard.fromJson(Map<String, dynamic> json) {
    return Flashcard(
      id: _string(json['id'], fallback: 'card_${json.hashCode}'),
      question: _string(json['question'], fallback: 'Untitled question'),
      answer: _string(json['answer'], fallback: 'No answer available.'),
      explanation: _optionalString(json['explanation']),
      order: _integer(json['order']),
      topic: _string(json['topic']),
      subject: _optionalString(json['subject']),
      classLevel: _optionalString(json['class_level']),
    );
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'id': id,
      'question': question,
      'answer': answer,
      if (explanation != null) 'explanation': explanation,
      'order': order,
      if (topic.isNotEmpty) 'topic': topic,
      if (subject != null) 'subject': subject,
      if (classLevel != null) 'class_level': classLevel,
    };
  }

  static String _string(Object? value, {String fallback = ''}) {
    if (value is String && value.trim().isNotEmpty) return value.trim();
    return fallback;
  }

  static String? _optionalString(Object? value) {
    if (value is String && value.trim().isNotEmpty) return value.trim();
    return null;
  }

  static int _integer(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return 0;
  }
}

/// A persisted set of flashcards returned by the backend.
@immutable
class FlashcardSet {
  const FlashcardSet({
    required this.id,
    required this.topic,
    this.subject,
    this.classLevel,
    this.status = 'ready',
    this.error,
    this.cards = const <Flashcard>[],
  });

  final String id;
  final String topic;
  final String? subject;
  final String? classLevel;
  final String status;
  final String? error;
  final List<Flashcard> cards;

  bool get isReady => status == 'ready' || cards.isNotEmpty;
  bool get isPending => status == 'pending' || status == 'processing';
  bool get isFailed => status == 'failed';

  factory FlashcardSet.fromJson(Map<String, dynamic> json) {
    final rawCards = json['flashcards'];
    final cards = rawCards is List
        ? rawCards
            .whereType<Map<String, dynamic>>()
            .map(Flashcard.fromJson)
            .toList(growable: false)
        : const <Flashcard>[];
    return FlashcardSet(
      id: Flashcard._string(json['id']),
      topic: Flashcard._string(json['topic'], fallback: 'Flashcards'),
      subject: Flashcard._optionalString(json['subject']),
      classLevel: Flashcard._optionalString(json['class_level']),
      status: Flashcard._string(json['status'], fallback: 'ready'),
      error: Flashcard._optionalString(json['error']),
      cards: cards,
    );
  }
}

/// Lightweight representation returned by the generate/list endpoints.
@immutable
class FlashcardSetSummary {
  const FlashcardSetSummary({
    required this.id,
    required this.topic,
    required this.flashcardCount,
    this.subject,
    this.classLevel,
    this.status = 'ready',
    this.createdAt,
  });

  final String id;
  final String topic;
  final int flashcardCount;
  final String? subject;
  final String? classLevel;
  final String status;
  final String? createdAt;

  bool get isFailed => status == 'failed';

  factory FlashcardSetSummary.fromJson(Map<String, dynamic> json) {
    return FlashcardSetSummary(
      id: Flashcard._string(json['id']),
      topic: Flashcard._string(json['topic'], fallback: 'Flashcards'),
      flashcardCount: Flashcard._integer(json['flashcard_count']),
      subject: Flashcard._optionalString(json['subject']),
      classLevel: Flashcard._optionalString(json['class_level']),
      status: Flashcard._string(json['status'], fallback: 'ready'),
      createdAt: Flashcard._optionalString(json['created_at']),
    );
  }
}
