/// Error handling for the classroom REST API (CONTRACTS §7).
///
/// The backend always answers failures with
/// `{"error": {"code", "message", "details", "retryable"}}`. Everything the UI
/// shows goes through [ApiException.friendlyMessage]; raw server messages are
/// only used when they are safe, human-readable validation feedback. Stack
/// traces and internal details never reach the screen.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.statusCode,
    this.details,
    this.retryable = false,
    this.isNetwork = false,
  });

  /// Parses the §7 envelope, falling back to HTTP-status-derived defaults.
  factory ApiException.fromEnvelope({
    required int statusCode,
    required Map<String, dynamic> body,
  }) {
    final error = body['error'];
    final map = error is Map ? Map<String, dynamic>.from(error) : null;
    final rawCode = map?['code'];
    final rawMessage = map?['message'];
    final rawDetails = map?['details'];
    final rawRetryable = map?['retryable'];
    final code = rawCode is String && rawCode.isNotEmpty
        ? rawCode
        : _codeFromStatus(statusCode);
    final message = rawMessage is String && rawMessage.isNotEmpty
        ? rawMessage
        : _messageFromStatus(statusCode);
    final retryable = rawRetryable is bool
        ? rawRetryable
        : (statusCode == 429 || statusCode == 503 || statusCode >= 500);
    return ApiException(
      code: code,
      message: message,
      statusCode: statusCode,
      details: rawDetails is Map
          ? Map<String, dynamic>.from(rawDetails)
          : null,
      retryable: retryable,
    );
  }

  /// Transport-level failure: offline, DNS, reset connection, timeout.
  factory ApiException.network([Object? cause]) => ApiException(
        code: 'network_error',
        message: cause?.toString() ?? 'Network request failed',
        retryable: true,
        isNetwork: true,
      );

  factory ApiException.unauthorized() => const ApiException(
        code: 'unauthorized',
        message: 'Your session is no longer valid.',
        statusCode: 401,
        retryable: false,
      );

  /// Stable machine code from §7 (`unauthorized`, `validation_error`, …).
  final String code;

  /// Server-provided human message. May be technical; use
  /// [friendlyMessage] for display.
  final String message;
  final int? statusCode;
  final Map<String, dynamic>? details;
  final bool retryable;
  final bool isNetwork;

  /// True when the client should sign the student out and show the auth gate.
  bool get isAuthFailure =>
      code == 'unauthorized' || (statusCode == 401) || (statusCode == 403);

  /// Message safe to show in the UI, never a raw stack trace.
  String get friendlyMessage {
    switch (code) {
      case 'unauthorized':
        return 'Your session expired. Please sign in again.';
      case 'forbidden':
        return "You don't have access to that classroom resource.";
      case 'not_found':
        return "We couldn't find that lesson or note. It may have been removed.";
      case 'conflict':
        return 'That action conflicts with the current lesson state. '
            'Refresh and try again.';
      case 'invalid_arguments':
      case 'validation_error':
        return _safeServerMessage ??
            'That input is not valid. Please check the details and try again.';
      case 'rate_limited':
        return 'Too many requests right now. Please wait a moment and retry.';
      case 'qwen_unavailable':
        return 'The live tutor is temporarily unavailable. '
            'Please try again in a few seconds.';
      case 'internal_error':
        return 'Something went wrong on our side. '
            'Your lesson is safe — please try again.';
      case 'network_error':
        return "Can't reach the classroom right now. "
            'Check your connection and try again.';
      default:
        if (statusCode == 401) {
          return 'Your session expired. Please sign in again.';
        }
        if (statusCode != null && statusCode! >= 500) {
          return 'The classroom is having trouble right now. Please retry.';
        }
        return 'Something went wrong. Please try again.';
    }
  }

  /// Server text is only trusted for input validation, and only when it looks
  /// like a sentence rather than a dump (no tracebacks, no file paths).
  String? get _safeServerMessage {
    final text = message.trim();
    if (text.isEmpty || text.length > 240) return null;
    final lowered = text.toLowerCase();
    const forbidden = <String>[
      'traceback',
      'exception',
      'stack trace',
      'file "',
      'c:\\',
      '/usr/',
      'sqlalchemy',
      'pydantic',
    ];
    for (final needle in forbidden) {
      if (lowered.contains(needle)) return null;
    }
    if (text.contains('\n')) return null;
    return text;
  }

  /// Short, technical one-liner for logs and `debugPrint` — never shown raw.
  String get debugLabel {
    final status = statusCode == null ? '' : ' [$statusCode]';
    return 'ApiException($code$status): $message';
  }

  @override
  String toString() => debugLabel;
}

/// Maps any thrown error to a friendly, display-ready sentence.
String friendlyErrorMessage(Object error) {
  if (error is ApiException) {
    return error.friendlyMessage;
  }
  return 'Something went wrong. Please try again.';
}

String _codeFromStatus(int statusCode) {
  switch (statusCode) {
    case 401:
      return 'unauthorized';
    case 403:
      return 'forbidden';
    case 404:
      return 'not_found';
    case 409:
      return 'conflict';
    case 422:
      return 'validation_error';
    case 429:
      return 'rate_limited';
    case 503:
      return 'qwen_unavailable';
    default:
      return statusCode >= 500 ? 'internal_error' : 'invalid_arguments';
  }
}

String _messageFromStatus(int statusCode) {
  switch (statusCode) {
    case 401:
      return 'Authentication required';
    case 403:
      return 'Forbidden';
    case 404:
      return 'Not found';
    case 409:
      return 'Conflict';
    case 422:
      return 'Invalid input';
    case 429:
      return 'Rate limited';
    case 503:
      return 'Tutor temporarily unavailable';
    default:
      return 'Unexpected server error';
  }
}
