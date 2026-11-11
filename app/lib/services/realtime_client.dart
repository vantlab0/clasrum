import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../core/config.dart';
import '../core/errors.dart';
import '../core/storage.dart';

/// Streamed message from the server (CONTRACTS §6 server→client).
enum RealtimeMessageType {
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
  Stream<ConnectionState> get connectionState =>
      _connectionController.stream;

  /// Friendly error messages surfaced to the UI.
  Stream<String> get errors => _errorController.stream;

  // ------------------------------------------------------------------ state --

  ConnectionState _state = ConnectionState.disconnected;
  ConnectionState get state => _state;

  String? _lessonId;
  String? get lessonId => _lessonId;

  String? _leaseId;
  String? get leaseId => _leaseId;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _heartbeat;
  bool _disposed = false;

  // ----------------------------------------------------------------- connect --

  /// Open the WebSocket for [lessonId] using the stored JWT.
  ///
  /// Throws [ApiException] on auth/transport failure so callers can show a
  /// friendly message and never leak stack traces.
  Future<void> connect({required String lessonId}) async {
    _disposed = false;
    _lessonId = lessonId;
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
          'Could not open the classroom socket.');
      return;
    }

    _subscription = _channel!.stream.listen(
      _onData,
      onError: _onError,
      onDone: _onDone,
      cancelOnError: false,
    );

    _startHeartbeat();
    _setState(ConnectionState.connected);
  }

  /// Close the socket cleanly.
  Future<void> disconnect() async {
    _stopHeartbeat();
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
    final type = _parseType(rawType);
    final message = RealtimeMessage(type: type, payload: decoded);

    switch (type) {
      case RealtimeMessageType.sessionReady:
        _leaseId = decoded['lease_id'] as String?;
        break;
      case RealtimeMessageType.sessionInterrupted:
        _setState(ConnectionState.interrupted);
        break;
      case RealtimeMessageType.sessionEnded:
        _setState(ConnectionState.disconnected);
        break;
      case RealtimeMessageType.error:
        _errorController.add(decoded['message'] as String? ??
            'The classroom reported an error.');
        break;
      case RealtimeMessageType.pong:
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
        'The classroom connection encountered an error.');
  }

  void _onDone() {
    if (_disposed) return;
    _stopHeartbeat();
    _channel = null;
    _subscription = null;
    _lessonId = null;
    _leaseId = null;
    if (!_disposed) _setState(ConnectionState.disconnected);
  }

  void _fail(
    ConnectionState next,
    String code,
    String message,
  ) {
    _stopHeartbeat();
    _channel = null;
    _subscription = null;
    _lessonId = null;
    _leaseId = null;
    _errorController.add(message);
    _setState(next);
  }

  void _setState(ConnectionState next) {
    if (_state == next) return;
    _state = next;
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

  static RealtimeMessageType _parseType(String? raw) {
    switch (raw) {
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
      case 'error':
        return RealtimeMessageType.error;
      case 'pong':
        return RealtimeMessageType.pong;
      default:
        return RealtimeMessageType.error;
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _stopHeartbeat();
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
}
