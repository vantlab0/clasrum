import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/config.dart';
import '../core/errors.dart';
import '../core/storage.dart';
import '../blackboard/model/board_geometry.dart';

/// Streamed message from the server (CONTRACTS §6 server→client).
enum RealtimeMessageType {
  connected,
  sessionReady,
  boardEvent,
  boardEvents,
  boardSnapshot,
  audioDelta,
  audioDone,
  audioTruncated,
  teacherState,
  transcriptEntry,
  lessonState,
  sessionInterrupted,
  sessionEnded,
  error,
  pong,
  viewportFocus,
  startupStatus,
  unknown,
}

/// A normalized message received from the realtime WebSocket.
class RealtimeMessage {
  const RealtimeMessage({
    required this.type,
    required this.payload,
  });

  final RealtimeMessageType type;
  final Map<String, dynamic> payload;

  /// Convenience accessors.
  String? get lessonId => payload['lesson_id'] as String?;
  String? get leaseId => payload['lease_id'] as String?;
  Map<String, dynamic>? get board => payload['board'] is Map
      ? Map<String, dynamic>.from(payload['board'] as Map)
      : null;
  List<dynamic>? get boardEvents => payload['events'] is List
      ? List<dynamic>.from(payload['events'] as List)
      : null;
  String? get audioData => payload['data'] as String?;
  int? get audioSampleRate => payload['sample_rate'] is int
      ? payload['sample_rate'] as int
      : (payload['sample_rate'] is num
          ? (payload['sample_rate'] as num).toInt()
          : null);
  int? get audioSequence => payload['sequence'] is int
      ? payload['sequence'] as int
      : (payload['sequence'] is num
          ? (payload['sequence'] as num).toInt()
          : null);
  bool get teacherSpeaking => payload['speaking'] == true;
  bool get teacherListening => payload['listening'] == true;
  bool get teacherThinking => payload['thinking'] == true;
  String? get errorCode => payload['code'] as String?;
  String? get errorMessage => payload['message'] as String?;
  bool get errorRetryable => payload['retryable'] == true;
  int? get transcriptCursor => payload['transcript_cursor'] is int
      ? payload['transcript_cursor'] as int
      : (payload['transcript_cursor'] is num
          ? (payload['transcript_cursor'] as num).toInt()
          : null);

  /// Viewport-focus message fields (server→client, Phase 3).
  String? get viewportTargetId => payload['target_id'] as String?;
  double? get viewportScale => payload['scale'] is num
      ? (payload['scale'] as num).toDouble()
      : (payload['scale'] is String
          ? double.tryParse(payload['scale'].toString())
          : null);
  BoardPoint? get viewportPosition =>
      BoardPoint.tryParse(payload['position'] ?? payload['at']);
  BoardRect? get viewportRegion => BoardRect.tryParse(payload['region']);
  bool get viewportAnimate => payload['animate'] != false;
  List<String>? get viewportTargetIds {
    final raw = payload['target_ids'];
    if (raw is List) {
      return raw.whereType<String>().toList(growable: false);
    }
    final id = viewportTargetId;
    return id == null ? null : <String>[id];
  }
}

/// Connection state exposed to the UI.
enum ConnectionState {
  disconnected,
  connecting,
  connected,
  interrupted,
  error,
}

/// Client side of the realtime WebSocket protocol (CONTRACTS §6).
///
/// Connects to `GET /api/v1/ws/lessons/{lesson_id}?token=<jwt>`, sends mic PCM16
/// frames + control messages, and exposes broadcast streams for:
/// board events, audio deltas, transcript entries, lesson states, teacher states,
/// connection states.
///
/// Audio and rendering are independent: nothing awaits audio before painting.
class RealtimeClient extends ChangeNotifier {
  RealtimeClient({
    required this.config,
    required this.storage,
    this.httpClient,
  });

  final AppConfig config;
  final AppStorage storage;
  final http.Client? httpClient;

  // ----------------------------------------------------------------- streams --

  final StreamController<RealtimeMessage> _messageController =
      StreamController<RealtimeMessage>.broadcast();
  final StreamController<ConnectionState> _connectionController =
      StreamController<ConnectionState>.broadcast();
  final StreamController<String> _errorController =
      StreamController<String>.broadcast();

  /// All decoded server messages.
  Stream<RealtimeMessage> get messages => _messageController.stream;

  /// Connection lifecycle.
  Stream<ConnectionState> get connectionState => _connectionController.stream;

  /// Friendly error messages surfaced to the UI.
  Stream<String> get errors => _errorController.stream;

  // ------------------------------------------------------------------ state --

  ConnectionState _state = ConnectionState.disconnected;
  ConnectionState get state => _state;

  String? _lessonId;
  String? get lessonId => _lessonId;

  String? _leaseId;
  String? get leaseId => _leaseId;

  String? _lastError;
  String? get lastError => _lastError;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _heartbeat;
  Timer? _connectTimeout;
  bool _disposed = false;

  // ----------------------------------------------------------------- connect --

  /// Open the WebSocket for [lessonId] using the stored JWT.
  ///
  /// Throws [ApiException] on auth/transport failure so callers can show a
  /// friendly message and never leak stack traces.
  Future<void> connect({required String lessonId}) async {
    _disposed = false;
    _lessonId = lessonId;
    _lastError = null;
    final token = storage.token;
    if (token == null) {
      _fail(ConnectionState.disconnected, 'unauthorized',
          'No auth token stored. Sign in first.');
      return;
    }

    _setState(ConnectionState.connecting);

    final uri = config.lessonSocketUri(lessonId, token: token);
    try {
      _channel = WebSocketChannel.connect(uri);
    } catch (e) {
      _fail(ConnectionState.disconnected, 'network_error',
          'Unable to connect. Check your internet connection.');
      return;
    }

    _subscription = _channel!.stream.listen(
      _onData,
      onError: _onError,
      onDone: _onDone,
      cancelOnError: false,
    );

    _startHeartbeat();
    _connectTimeout?.cancel();
    _connectTimeout = Timer(const Duration(seconds: 30), () {
      if (_disposed) return;
      if (_state == ConnectionState.connecting) {
        _fail(ConnectionState.error, 'timeout',
            'The realtime session timed out. Try reconnecting.');
      }
    });
  }

  /// Close the socket cleanly.
  Future<void> disconnect() async {
    _stopHeartbeat();
    _connectTimeout?.cancel();
    _connectTimeout = null;
    await _subscription?.cancel();
    _subscription = null;
    await _channel?.sink.close();
    _channel = null;
    _lessonId = null;
    _leaseId = null;
    if (!_disposed) _setState(ConnectionState.disconnected);
  }

  /// Barge-in: cancel the tutor response and truncate playback.
  Future<void> interrupt() async {
    await _send({'type': 'session.interrupt'});
  }

  /// End the lesson on the socket.
  Future<void> endSession({String? reason}) async {
    await _send({'type': 'session.end', if (reason != null) 'reason': reason});
  }

  /// Request a full board snapshot from the server.
  Future<void> requestSnapshot() async {
    await _send({'type': 'board.request_snapshot'});
  }

  /// Request backfill of events after [afterSequence] (used on reconnect/gap).
  Future<void> backfill({required int afterSequence}) async {
    await _send({'type': 'board.backfill', 'after_sequence': afterSequence});
  }

  /// Send a ping for latency/keepalive.
  Future<void> ping() async {
    await _send({'type': 'ping', 't': DateTime.now().millisecondsSinceEpoch});
  }

  // ---------------------------------------------------------- audio stream --

  /// Stream raw PCM16 microphone frames to the backend (16 kHz mono).
  ///
  /// The caller is responsible for opening the mic (package `record`) and feeding
  /// frames here. Nothing awaits audio before board painting.
  void sendAudioFrame(Uint8List pcm16) {
    if (_channel == null || _disposed) return;
    try {
      _channel!.sink.add(pcm16);
    } catch (e) {
      _debug('audio send failed: $e');
    }
  }

  /// Signal that the mic stream is starting.
  Future<void> audioStart() async {
    await _send({
      'type': 'audio.start',
      'sample_rate': 16000,
      'format': 'pcm16',
    });
  }

  /// Signal that the mic stream is stopping (triggers a tutor response).
  Future<void> audioStop() async {
    await _send({'type': 'audio.stop'});
  }

  // ------------------------------------------------------------------- send --

  Future<void> _send(Map<String, dynamic> message) async {
    if (_channel == null || _disposed) return;
    try {
      _channel!.sink.add(jsonEncode(message));
    } catch (e) {
      _debug('ws send failed: $e');
      _fail(ConnectionState.error, 'network_error',
          'The classroom connection was lost.');
    }
  }

  // ------------------------------------------------------------ receive --

  void _onData(dynamic data) {
    if (_disposed) return;
    if (data is String) {
      try {
        final decoded = jsonDecode(data) as Map<String, dynamic>;
        _handle(decoded);
      } catch (e) {
        _debug('ws text decode failed: $e');
      }
    } else if (data is Uint8List || data is List<int>) {
      // Binary frames carry audio from the client side only; the server sends
      // JSON text frames. Ignore unexpected binary.
      _debug('ignored binary frame of length ${data.length}');
    }
  }

  void _handle(Map<String, dynamic> decoded) {
    final rawType = decoded['type'] as String?;
    final type = parseType(rawType);
    final message = RealtimeMessage(type: type, payload: decoded);

    switch (type) {
      case RealtimeMessageType.connected:
        _leaseId = decoded['lease_id'] as String? ?? _leaseId;
        _debug(
            'ws connected ack lesson=${decoded['lesson_id']} lease=$_leaseId');
        break;
      case RealtimeMessageType.sessionReady:
        _leaseId = decoded['lease_id'] as String?;
        _connectTimeout?.cancel();
        _connectTimeout = null;
        if (_state == ConnectionState.connecting) {
          _setState(ConnectionState.connected);
        }
        break;
      case RealtimeMessageType.sessionInterrupted:
        _setState(ConnectionState.interrupted);
        break;
      case RealtimeMessageType.sessionEnded:
        _setState(ConnectionState.disconnected);
        break;
      case RealtimeMessageType.error:
        final message =
            decoded['message'] as String? ?? 'The classroom reported an error.';
        final code = decoded['code'] as String?;
        final details = decoded['details'];
        final detailText = details is String
            ? details
            : (details is Map && details['error'] is String
                ? details['error'] as String
                : null);
        final parts = <String>[
          message,
          if (code != null && code.isNotEmpty && !message.contains(code))
            '[$code]',
          if (detailText != null &&
              detailText.isNotEmpty &&
              !message.contains(detailText))
            detailText,
        ];
        final full = parts.join(' ').trim();
        _lastError = full;
        _errorController.add(full);
        break;
      case RealtimeMessageType.pong:
        break;
      case RealtimeMessageType.unknown:
        _debug('ignored unknown ws message type=${decoded['type']}');
        break;
      default:
        break;
    }

    if (!_disposed) {
      _messageController.add(message);
    }
  }

  void _onError(Object error, StackTrace? stack) {
    if (_disposed) return;
    _debug('ws error: $error');
    _fail(ConnectionState.error, 'network_error',
        'Voice connection interrupted. Check your internet connection and retry.');
  }

  void _onDone() {
    if (_disposed) return;
    _stopHeartbeat();
    _connectTimeout?.cancel();
    _connectTimeout = null;
    final wasConnecting = _state == ConnectionState.connecting;
    int? closeCode;
    String? closeReason;
    try {
      final dynamic ch = _channel;
      closeCode = ch?.closeCode as int?;
      closeReason = ch?.closeReason as String?;
    } catch (_) {}
    _channel = null;
    _subscription = null;
    _lessonId = null;
    _leaseId = null;
    if (wasConnecting) {
      if (_lastError == null) {
        final detail = <String>[
          'The realtime session ended before it was ready. Try reconnecting.',
          if (closeCode != null) 'code=$closeCode',
          if (closeReason != null && closeReason.isNotEmpty) closeReason,
        ].join(' ');
        _lastError = detail;
        _errorController.add(detail);
      }
      _setState(ConnectionState.error);
      return;
    }
    if (!_disposed) _setState(ConnectionState.disconnected);
  }

  void _fail(
    ConnectionState next,
    String code,
    String message,
  ) {
    _stopHeartbeat();
    _connectTimeout?.cancel();
    _connectTimeout = null;
    _channel = null;
    _subscription = null;
    _lessonId = null;
    _leaseId = null;
    _lastError = message;
    _errorController.add(message);
    _setState(next);
  }

  void _setState(ConnectionState next) {
    if (_disposed) return;
    if (_state == next) return;
    _state = next;
    if (!_connectionController.isClosed) {
      _connectionController.add(next);
    }
    notifyListeners();
  }

  void _startHeartbeat() {
    _stopHeartbeat();
    _heartbeat = Timer.periodic(const Duration(seconds: 20), (_) {
      if (_disposed) return;
      ping();
    });
  }

  void _stopHeartbeat() {
    _heartbeat?.cancel();
    _heartbeat = null;
  }

  void _debug(String message) {
    if (kDebugMode) {
      debugPrint('[RealtimeClient] $message');
    }
  }

  @visibleForTesting
  static RealtimeMessageType parseType(String? raw) {
    switch (raw) {
      case 'connected':
        return RealtimeMessageType.connected;
      case 'session.ready':
        return RealtimeMessageType.sessionReady;
      case 'board.event':
        return RealtimeMessageType.boardEvent;
      case 'board.events':
        return RealtimeMessageType.boardEvents;
      case 'board.snapshot':
        return RealtimeMessageType.boardSnapshot;
      case 'audio.delta':
        return RealtimeMessageType.audioDelta;
      case 'audio.done':
        return RealtimeMessageType.audioDone;
      case 'audio.truncated':
        return RealtimeMessageType.audioTruncated;
      case 'teacher.state':
        return RealtimeMessageType.teacherState;
      case 'transcript.entry':
        return RealtimeMessageType.transcriptEntry;
      case 'lesson.state':
        return RealtimeMessageType.lessonState;
      case 'session.interrupted':
        return RealtimeMessageType.sessionInterrupted;
      case 'session.ended':
        return RealtimeMessageType.sessionEnded;
      case 'viewport.focus':
        return RealtimeMessageType.viewportFocus;
      case 'startup.status':
        return RealtimeMessageType.startupStatus;
      case 'error':
      case 'session.error':
        return RealtimeMessageType.error;
      case 'pong':
        return RealtimeMessageType.pong;
      case 'session.updated':
      case 'session.created':
        return RealtimeMessageType.unknown;
      default:
        return RealtimeMessageType.unknown;
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _stopHeartbeat();
    _connectTimeout?.cancel();
    _connectTimeout = null;
    unawaited(_subscription?.cancel());
    _subscription = null;
    _channel?.sink.close();
    _channel = null;
    _messageController.close();
    _connectionController.close();
    _errorController.close();
    super.dispose();
  }
}

/// Dependency key: the [RealtimeClient] is provided at the shell so every
/// lesson screen shares one socket per lesson.
class RealtimeClientScope extends InheritedNotifier<RealtimeClient> {
  const RealtimeClientScope({
    super.key,
    required RealtimeClient notifier,
    required super.child,
  }) : super(notifier: notifier);

  static RealtimeClient of(BuildContext context) {
    final scope =
        context.dependOnInheritedWidgetOfExactType<RealtimeClientScope>();
    if (scope == null) {
      throw StateError('RealtimeClientScope not found in the widget tree.');
    }
    return scope.notifier!;
  }

  static RealtimeClient? maybeOf(BuildContext context) {
    return context
        .dependOnInheritedWidgetOfExactType<RealtimeClientScope>()
        ?.notifier;
  }
}
