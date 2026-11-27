import 'dart:async';
import 'dart:math' as math;
import 'dart:ui';

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart' hide ConnectionState;
import 'package:record/record.dart';

import '../../blackboard/blackboard_view.dart';
import '../../blackboard/model/board_document.dart';
import '../../blackboard/model/board_event.dart';
import '../../blackboard/model/board_geometry.dart';
import '../../blackboard/runtime/blackboard_controller.dart';
import '../../blackboard/runtime/board_fullness_detector.dart';
import '../../blackboard/runtime/board_viewport_controller.dart';
import '../../core/theme.dart';
import '../../core/widgets/pwony_icons.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../services/realtime_client.dart';
import 'ai_audio_playback.dart';

/// Live classroom screen: immersive full-screen blackboard experience.
class LiveScreen extends StatefulWidget {
  const LiveScreen({super.key, required this.client});

  final RealtimeClient client;

  @override
  State<LiveScreen> createState() => _LiveScreenState();
}

enum _MicPhase { idle, listening, submitting }

class _LiveScreenState extends State<LiveScreen>
    with WidgetsBindingObserver, TickerProviderStateMixin {
  String? _lessonId;
  bool _connecting = false;
  String? _error;
  String _connectionStatus = 'Connecting to Qwen model...';
  String _connectionDetail = 'Starting realtime classroom session...';
  double? _connectionProgress;
  bool _waitingForTutor = false;
  bool _receivedTeachingContent = false;
  bool _receivedBoardContent = false;
  bool _tutorAudioStarted = false;
  String _startupStatusMessage = 'Preparing your first lesson...';
  Timer? _tutorStartTimer;
  Timer? _connectProgressTimer;

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _audioStreamSub;
  StreamSubscription<Amplitude>? _amplitudeSub;
  bool _speechDetected = false;
  // Manual-turn mic: locked until the backend sends mic.unlock after an
  // explicit, answer-requiring teacher question.
  bool _micContinuous = false;
  bool _micUnlockedForQuestion = false;
  String? _activeMicRequestId;
  double _noiseFloorDb = -70.0;
  bool _submittingTurn = false;
  DateTime? _speechStartedAt;
  DateTime? _lastVoiceAt;
  int _capturedBytes = 0;
  Timer? _vadTimer;
  final List<double> _waveLevels = List<double>.filled(18, 0.08);

  bool _paused = false;
  _MicPhase _micPhase = _MicPhase.idle;

  bool _cameraOpening = false;
  bool _cameraActive = false;
  bool _imageSubmitting = false;
  CameraController? _cameraController;
  String? _cameraError;

  late final BlackboardController _boardController;
  late final BoardViewportController _viewportController;
  BoardFullnessSnapshot? _lastBoardFullness;
  int _reportedFullSequence = -1;
  late final AnimationController _pulseController;
  late final AiAudioPlaybackController _audioPlayback;

  Size? _lastViewportSize;

  StreamSubscription<ConnectionState>? _connSub;
  StreamSubscription<String>? _errSub;
  StreamSubscription<RealtimeMessage>? _msgSub;

  static const Duration _silenceThreshold = Duration(milliseconds: 1400);
  static const Duration _maxListeningDuration = Duration(seconds: 20);
  static const double _speechDbThreshold = -45; // more robust to quiet voices

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _boardController = BlackboardController();
    _viewportController = BoardViewportController(tickerProvider: this);
    // Track board content bounds so the viewport can clamp panning.
    _boardController.addListener(() {
      if (!mounted) return;
      final bounds = _boardController.measuredContentBounds();
      _viewportController.onContentBoundsChanged(bounds);
      // Measured bounds are written during paint; check after that frame.
      WidgetsBinding.instance
          .addPostFrameCallback((_) => _recheckBoardFullness());
    });
    _viewportController.addListener(_recheckBoardFullness);
    _boardController.onBackfillRequested = (afterSequence) {
      unawaited(widget.client.backfill(afterSequence: afterSequence));
    };
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
    _audioPlayback = AiAudioPlaybackController();
    _lessonId = widget.client.lessonId;
    _connecting = widget.client.state == ConnectionState.connecting;
    _syncConnectionUi(widget.client.state);

    _connSub = widget.client.connectionState.listen((state) {
      if (!mounted) return;
      if (state == ConnectionState.disconnected ||
          state == ConnectionState.error ||
          state == ConnectionState.interrupted) {
        unawaited(_audioPlayback.reset(endState: AiPlaybackState.interrupted));
        if (state != ConnectionState.interrupted) {
          _paused = false;
        }
      }
      setState(() {
        _lessonId ??= widget.client.lessonId;
        _syncConnectionUi(state);
        switch (state) {
          case ConnectionState.connected:
            _error = null;
            // The microphone stays locked until a direct teacher question
            // arrives as mic.unlock.
            break;
          case ConnectionState.connecting:
            break;
          case ConnectionState.interrupted:
            _error = 'Another device took over this lesson.';
          case ConnectionState.error:
          case ConnectionState.disconnected:
            break;
        }
      });
    });
    _errSub = widget.client.errors.listen((message) {
      if (!mounted) return;
      final lower = message.toLowerCase();
      final isStartupDelay = lower.contains('not started drawing yet') ||
          lower.contains('tutor_start_delayed') ||
          lower.contains('first board visual is still being prepared');
      if (isStartupDelay && !_receivedBoardContent) {
        setState(() {
          _startupStatusMessage = _tutorAudioStarted
              ? 'Creating the first board illustration...'
              : 'Preparing your first lesson...';
          _waitingForTutor = true;
          // Do not show a scary centered error for startup delays.
          _error = null;
        });
        return;
      }
      setState(() => _error = message);
    });
    _msgSub = widget.client.messages.listen(_handleMessage);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _vadTimer?.cancel();
    _connectProgressTimer?.cancel();
    _tutorStartTimer?.cancel();
    unawaited(_connSub?.cancel());
    unawaited(_errSub?.cancel());
    unawaited(_msgSub?.cancel());
    _micContinuous = false;
    unawaited(_stopMic(discard: true, silent: true, notifyServer: false));
    unawaited(_disposeCamera());
    unawaited(_audioPlayback.reset(endState: AiPlaybackState.idle));
    _audioPlayback.dispose();
    _pulseController.dispose();
    _viewportController.removeListener(_recheckBoardFullness);
    _boardController.dispose();
    _viewportController.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive ||
        state == AppLifecycleState.detached) {
      // Keep session/board intact; stop mic capture to avoid leaving it active.
      if (_micPhase == _MicPhase.listening) {
        _micContinuous = false;
        unawaited(_stopMic(discard: true, silent: true, notifyServer: false));
      }
      // Avoid background playback surprises; preserve board/session/realtime state.
      if (_audioPlayback.state == AiPlaybackState.playing ||
          _audioPlayback.state == AiPlaybackState.buffering) {
        unawaited(_audioPlayback.pause());
        if (mounted) {
          setState(() => _paused = true);
        } else {
          _paused = true;
        }
      }
    }
  }

  bool get _canRetryConnection {
    final lessonId = _lessonId ?? widget.client.lessonId;
    return lessonId != null;
  }

  Future<void> _retryConnection() async {
    final lessonId = _lessonId ?? widget.client.lessonId;
    if (lessonId == null) return;
    setState(() {
      _error = null;
    });
    try {
      await widget.client.disconnect();
    } catch (_) {}
    await widget.client.connect(lessonId: lessonId);
  }

  void _startTutorWaitTimer() {
    _tutorStartTimer?.cancel();
    _receivedTeachingContent = false;
    _receivedBoardContent = false;
    _tutorAudioStarted = false;
    _waitingForTutor = false;
    _tutorStartTimer = Timer(const Duration(seconds: 8), () {
      if (!mounted || _receivedBoardContent) return;
      setState(() {
        _waitingForTutor = true;
        if (_tutorAudioStarted) {
          _startupStatusMessage = 'Creating the first board illustration...';
        } else if (_receivedTeachingContent) {
          _startupStatusMessage = 'Preparing your first lesson...';
        } else {
          _startupStatusMessage = 'Waiting for tutor response...';
        }
      });
    });
  }

  void _markTeachingContentReceived({bool triggerSetState = true}) {
    final wasWaiting = _waitingForTutor;
    final oldMsg = _startupStatusMessage;
    _receivedTeachingContent = true;
    if (_receivedBoardContent) {
      _waitingForTutor = false;
      _tutorStartTimer?.cancel();
      _tutorStartTimer = null;
    } else {
      _waitingForTutor = true;
      if (_tutorAudioStarted) {
        _startupStatusMessage = 'Creating the first board illustration...';
      } else {
        _startupStatusMessage = 'Preparing your first lesson...';
      }
    }
    final changed = wasWaiting != _waitingForTutor || oldMsg != _startupStatusMessage;
    if (triggerSetState && mounted && changed) setState(() {});
  }

  void _markBoardContentReceived({bool triggerSetState = true}) {
    if (_receivedBoardContent) return;
    _receivedBoardContent = true;
    _receivedTeachingContent = true;
    _waitingForTutor = false;
    _tutorStartTimer?.cancel();
    _tutorStartTimer = null;
    if (triggerSetState && mounted) setState(() {});
  }

  void _syncConnectionUi(ConnectionState state) {
    switch (state) {
      case ConnectionState.connecting:
        _connecting = true;
        _connectionStatus = 'Connecting to Qwen...';
        _connectionDetail = 'Establishing realtime session...';
        _connectionProgress = null; // indeterminate - no fake progress
        _connectProgressTimer?.cancel();
        _connectProgressTimer = null;
        break;
      case ConnectionState.connected:
        _connecting = false;
        _connectionStatus = 'Connected';
        _connectionDetail = 'Qwen model is ready.';
        _connectionProgress = 1.0;
        _connectProgressTimer?.cancel();
        _connectProgressTimer = null;
        break;
      case ConnectionState.interrupted:
        _connecting = false;
        _connectProgressTimer?.cancel();
        _connectProgressTimer = null;
        _connectionProgress = null;
        _tutorStartTimer?.cancel();
        _waitingForTutor = false;
        break;
      case ConnectionState.error:
      case ConnectionState.disconnected:
        _connecting = false;
        _connectProgressTimer?.cancel();
        _connectProgressTimer = null;
        _connectionProgress = null;
        _tutorStartTimer?.cancel();
        _waitingForTutor = false;
        break;
    }
  }

  /// Uses the renderer's measured bounds to decide whether the student's
  /// visible board screen is full. A report is sent at most once per sequence;
  /// the server owns the actual wipe.
  void _recheckBoardFullness() {
    if (!mounted || _connecting || _boardController.isAnimating ||
        _viewportController.isManuallyInteracting) {
      return;
    }
    final document = _boardController.document;
    if (document.visibleElements.isEmpty) {
      _lastBoardFullness = null;
      return;
    }
    final aliveIds = document.visibleElements.map((e) => e.elementId).toSet();
    _boardController.renderCache.bounds.retainOnly(aliveIds);
    final boxes = <BoardRect>[];
    for (final id in aliveIds) {
      final measured = _boardController.renderCache.bounds[id] ??
          document.boundsOf(<String>[id]);
      if (measured != null && !measured.isEmpty) boxes.add(measured);
    }
    if (boxes.isEmpty) return;
    final snapshot = BoardFullnessDetector.snapshot(
      visibleRect: _viewportController.visibleRect,
      elementBounds: boxes,
      sequence: _boardController.appliedSequence,
      wasFull: _lastBoardFullness?.isFull ?? false,
    );
    _lastBoardFullness = snapshot;
    if (!snapshot.isFull || snapshot.sequence <= _reportedFullSequence) return;
    _reportedFullSequence = snapshot.sequence;
    unawaited(widget.client.reportViewportFull(
      sequence: snapshot.sequence,
      coverage: snapshot.coverage,
      visibleRect: snapshot.visibleRect,
      scale: _viewportController.scale,
      offset: _viewportController.offset,
    ));
  }

  void _handleMessage(RealtimeMessage message) {
    if (!mounted) return;
    setState(() {
      _lessonId ??= widget.client.lessonId ?? message.lessonId;
    });
    switch (message.type) {
      case RealtimeMessageType.connected:
        if (mounted && _connecting) {
          setState(() {
            _connectionStatus = 'Connecting to Qwen...';
            _connectionDetail = 'Establishing realtime session...';
          });
        }
        break;
      case RealtimeMessageType.sessionReady:
        _applySessionReady(message.payload);
        _startTutorWaitTimer();
        break;
      case RealtimeMessageType.boardSnapshot:
        _applyBoardSnapshot(message.payload);
        if (_boardController.document.elements.isNotEmpty) {
          _markBoardContentReceived();
        }
        break;
      case RealtimeMessageType.boardEvent:
        _applyBoardEvent(message.payload);
        _markBoardContentReceived();
        break;
      case RealtimeMessageType.boardEvents:
        _applyBoardEvents(message.payload);
        _markBoardContentReceived();
        break;
      case RealtimeMessageType.lessonState:
        // Lesson metadata updates are ignored by the minimal classroom UI.
        break;
      case RealtimeMessageType.teacherState:
        // Teacher state is reflected implicitly by streamed audio/playback.
        break;
      case RealtimeMessageType.audioDelta:
        _tutorAudioStarted = true;
        _handleAudioDelta(message);
        _markTeachingContentReceived();
        break;
      case RealtimeMessageType.audioDone:
        _audioPlayback.markDone(
          responseId: _responseIdFromPayload(message.payload),
        );
        break;
      case RealtimeMessageType.audioTruncated:
        unawaited(_audioPlayback.interrupt(
          interruptedResponseId: _responseIdFromPayload(message.payload),
          keepNewer: true,
        ));
        break;
      case RealtimeMessageType.userSpeechStarted:
        // Legacy VAD telemetry. It never grants microphone access; local
        // amplitude detection handles barge-in during answer turns.
        unawaited(_audioPlayback.interrupt(keepNewer: true));
        if (mounted) {
          setState(() => _speechDetected = true);
        }
        break;
      case RealtimeMessageType.micUnlock:
        if (message.micRequiresAnswer) {
          unawaited(_unlockMicForQuestion(
            requestId: message.micRequestId ?? '',
            reason: message.micReason,
          ));
        }
        break;
      case RealtimeMessageType.micLock:
        unawaited(_lockMicLocally());
        break;
      case RealtimeMessageType.sessionEnded:
        unawaited(_audioPlayback.reset(endState: AiPlaybackState.completed));
        setState(() {
          _error = 'Lesson ended.';
        });
        break;
      case RealtimeMessageType.viewportFocus:
        _applyViewportFocus(message.payload);
        break;
      case RealtimeMessageType.startupStatus:
        final msg = message.payload['message']?.toString();
        final phase = message.payload['phase']?.toString();
        if (msg != null && msg.isNotEmpty) {
          setState(() {
            _startupStatusMessage = msg;
            if (!_receivedBoardContent) _waitingForTutor = true;
            if (phase == 'audio_started' || phase == 'recovering') {
              _receivedTeachingContent = true;
            }
          });
          if (phase == 'board_event_persisted' || phase == 'ready') {
            _markBoardContentReceived(triggerSetState: true);
          }
        }
        break;
      case RealtimeMessageType.sessionInterrupted:
        unawaited(_audioPlayback.reset(endState: AiPlaybackState.interrupted));
        _micContinuous = false;
        _micUnlockedForQuestion = false;
        _activeMicRequestId = null;
        unawaited(_stopMic(discard: true, silent: true, notifyServer: false));
        break;
      default:
        break;
    }
  }

  void _applySessionReady(Map<String, dynamic> payload) {
    final board = payload['board'];
    if (board is Map<String, dynamic>) {
      _applyBoardEnvelope(board);
    } else if (board is Map) {
      _applyBoardEnvelope(Map<String, dynamic>.from(board));
    }
    if (_boardController.document.elements.isNotEmpty) {
      _markTeachingContentReceived();
    }
  }

  void _applyBoardSnapshot(Map<String, dynamic> payload) {
    final board = payload['board'] ?? payload;
    if (board is Map<String, dynamic>) {
      _applyBoardEnvelope(board);
    } else if (board is Map) {
      _applyBoardEnvelope(Map<String, dynamic>.from(board));
    }
  }

  void _applyBoardEnvelope(Map<String, dynamic> envelope) {
    try {
      final doc = BoardDocument.fromSnapshotEnvelope(envelope);
      _boardController.loadSnapshot(doc);
    } catch (e) {
      // Keep board stable on malformed snapshots, but surface the actual error.
      if (!mounted) return;
      setState(() {
        _error = 'Board update failed: $e';
      });
    }
  }

  void _applyBoardEvent(Map<String, dynamic> payload) {
    final raw = payload['event'] ?? payload;
    if (raw is! Map) return;
    try {
      final event = BoardEvent.fromJson(Map<String, dynamic>.from(raw));
      debugPrint('[LiveScreen] board event received type=${event.type} seq=${event.sequence} id=${event.eventId}');
      _boardController.enqueue(event);
      debugPrint('[LiveScreen] board event enqueued/applied seq=${event.sequence}');
      if (_isFullBoardClear(event)) _recycleViewportAfterClear();
    } catch (e, st) {
      debugPrint('[LiveScreen] Failed to apply board event: $e\n$st');
    }
  }

  void _applyBoardEvents(Map<String, dynamic> payload) {
    final raw = payload['events'] ?? payload['board_events'];
    if (raw is! List) return;
    final events = <BoardEvent>[];
    for (final item in raw) {
      if (item is! Map) continue;
      try {
        events.add(BoardEvent.fromJson(Map<String, dynamic>.from(item)));
      } catch (e, st) {
        debugPrint(
            '[LiveScreen] Failed to parse board event in batch: $e\n$st');
      }
    }
    if (events.isNotEmpty) {
      _boardController.enqueueAll(events);
      if (events.any(_isFullBoardClear)) _recycleViewportAfterClear();
    }
  }

  bool _isFullBoardClear(BoardEvent event) {
    if (event.type == BoardEventType.clear.wireName) return true;
    if (event.type != BoardEventType.erase.wireName) return false;
    final raw = event.payload['scope'] ?? event.payloadParsed.raw['scope'];
    return raw?.toString().toLowerCase() == 'all';
  }

  /// Reuse the same visible board screen after a persisted wipe: drop stale
  /// measurements and return from a zoomed/panned window to the full board.
  void _recycleViewportAfterClear() {
    _lastBoardFullness = null;
    _reportedFullSequence = -1;
    _boardController.renderCache.bounds.clear();
    _viewportController.onContentBoundsChanged(null);
    _viewportController.setViewport(Offset.zero, 1.0);
  }

  /// Student-initiated clear. The server persists the event and broadcasts it;
  /// the normal board event path then recycles the visible viewport.
  Future<void> _onClearBoard() async {
    if (_connecting || _boardController.document.visibleElements.isEmpty) {
      return;
    }
    await widget.client.requestBoardClear(
      sequence: _boardController.appliedSequence,
    );
  }

  void _applyViewportFocus(Map<String, dynamic> payload) {
    final targetIds = <String>[];
    final rawTargetId = payload['target_id'];
    if (rawTargetId != null) targetIds.add(rawTargetId.toString());
    final rawTargetIds = payload['target_ids'];
    if (rawTargetIds is List) {
      for (final id in rawTargetIds) {
        if (id != null) targetIds.add(id.toString());
      }
    }

    BoardRect? region;
    final rawRegion = payload['region'];
    if (rawRegion is Map) {
      region = BoardRect.tryParse(rawRegion);
    }

    final position = BoardPoint.tryParse(payload['position'] ?? payload['at']);
    final scale = payload['scale'] is num
        ? (payload['scale'] as num).toDouble()
        : (payload['scale'] is String
            ? double.tryParse(payload['scale'].toString())
            : null);
    final animate = payload['animate'] != false;

    if (targetIds.isNotEmpty) {
      unawaited(_viewportController.focusOnElements(
        targetIds,
        document: _boardController.document,
        animate: animate,
      ));
    } else if (region != null) {
      unawaited(_viewportController.focusOnRect(
        region,
        animate: animate,
      ));
    } else if (position != null) {
      // Focus on a point: create a small rect around it.
      final pointRect = BoardRect(
        position.x - 0.02,
        position.y - 0.02,
        0.04,
        0.04,
      ).clamp01();
      unawaited(_viewportController.focusOnRect(
        pointRect,
        animate: animate,
      ));
    }
    if (scale != null && scale > 0) {
      // Apply requested scale after any pan.
      _viewportController.setViewport(
        _viewportController.offset,
        scale.clamp(0.5, 2.0),
      );
    }
  }

  void _handleAudioDelta(RealtimeMessage message) {
    // During an answer turn local amplitude detection flushes playback when
    // the student begins speaking.
    if (_micPhase == _MicPhase.submitting) return;
    unawaited(_enqueueAudioDelta(message));
  }

  Future<void> _enqueueAudioDelta(RealtimeMessage message) async {
    final data = message.audioData;
    if (data == null || data.isEmpty) return;
    try {
      if (!_paused) {
        // Ensure playback is enabled for incoming stream (safe to call repeatedly).
        await _audioPlayback.resume();
      }
      await _audioPlayback.enqueueDelta(
        base64Pcm16: data,
        sampleRate: message.audioSampleRate,
        sequence: message.audioSequence,
        responseId: _responseIdFromPayload(message.payload),
      );
      if (_paused) {
        // Keep buffered safely while preserving playback position/state.
        await _audioPlayback.pause();
      }
    } catch (e, st) {
      debugPrint('[LiveScreen] Audio frame/playback error: $e\n$st');
    }
  }

  int? _responseIdFromPayload(Map<String, dynamic> payload) {
    final value = payload['response_id'] ?? payload['responseId'];
    if (value is int) return value;
    if (value is num) return value.toInt();
    if (value is String) return int.tryParse(value);
    return null;
  }

  Future<void> _confirmExit() async {
    final shouldLeave = await showDialog<bool>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.55),
      builder: (context) => GlassModal(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Leave classroom?',
              style: TextStyle(
                color: AppColors.white,
                fontSize: 18,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              'You can return to this lesson later. Your board and session state stay intact.',
              style:
                  TextStyle(color: AppColors.muted, fontSize: 14, height: 1.4),
            ),
            const SizedBox(height: 18),
            Row(
              children: <Widget>[
                Expanded(
                  child: SecondaryActionButton(
                    onPressed: () => Navigator.of(context).pop(false),
                    label: 'Stay',
                    compact: true,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: PrimaryActionButton(
                    onPressed: () => Navigator.of(context).pop(true),
                    label: 'Leave',
                    compact: true,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    if (shouldLeave == true && mounted) {
      _micContinuous = false;
      await _stopMic(discard: true, silent: true, notifyServer: false);
      await _audioPlayback.reset(endState: AiPlaybackState.completed);
      await widget.client.disconnect();
      if (mounted) Navigator.of(context).maybePop(true);
    }
  }

  Future<void> _togglePause() async {
    if (_submittingTurn) return;
    final nextPaused = !_paused;
    setState(() {
      _paused = nextPaused;
    });
    if (nextPaused) {
      await _audioPlayback.pause();
    } else {
      await _audioPlayback.resume();
    }
  }

  Future<void> _onMicPressed() async {
    if (_connecting || _submittingTurn || !_micUnlockedForQuestion) return;
    if (_micPhase == _MicPhase.listening) {
      // Tap ends an answer turn; long-press cancels it and keeps the window open.
      await _finishListening(manualStop: true);
      return;
    }
    if (_micPhase == _MicPhase.idle) {
      await _startListening();
    }
  }

  Future<void> _cancelListening() async {
    if (_micPhase != _MicPhase.listening) return;
    // Clear the uncommitted audio, but keep the answer window unlocked so the
    // student can retry the same teacher question.
    await _stopMic(discard: true, silent: true);
    if (!mounted) return;
    setState(() => _micPhase = _MicPhase.idle);
  }

  /// Open the microphone only for the answer window granted by `mic.unlock`.
  Future<void> _unlockMicForQuestion({
    required String requestId,
    String? reason,
  }) async {
    if (!mounted || requestId.isEmpty) return;
    if (_connecting || _submittingTurn || _lessonId == null) return;
    if (_micPhase == _MicPhase.listening || _micPhase == _MicPhase.submitting) {
      return;
    }
    _micUnlockedForQuestion = true;
    _activeMicRequestId = requestId;
    if (mounted) {
      setState(() {
        _error = null;
        _waitingForTutor = false;
        if (reason != null && reason.isNotEmpty) {
          _startupStatusMessage = reason;
        }
      });
    }
    await _startListening();
  }

  Future<void> _lockMicLocally() async {
    _micUnlockedForQuestion = false;
    _activeMicRequestId = null;
    _micContinuous = false;
    await _stopMic(discard: true, silent: true, notifyServer: false);
  }

  Future<void> _startListening() async {
    final requestId = _activeMicRequestId;
    if (_lessonId == null ||
        !_micUnlockedForQuestion ||
        requestId == null ||
        requestId.isEmpty ||
        _micPhase != _MicPhase.idle ||
        _submittingTurn) {
      return;
    }
    if (_cameraActive || _cameraOpening) return;

    try {
      final hasPermission = await _recorder.hasPermission();
      if (!hasPermission) {
        if (!mounted) return;
        setState(() => _error = 'Microphone permission is required to answer.');
        return;
      }

      setState(() {
        _micPhase = _MicPhase.listening;
        _micContinuous = true;
        _noiseFloorDb = -70.0;
        _speechDetected = false;
        _capturedBytes = 0;
        _speechStartedAt = DateTime.now();
        _lastVoiceAt = null;
        _error = null;
        for (var i = 0; i < _waveLevels.length; i++) {
          _waveLevels[i] = 0.08;
        }
      });

      await widget.client.audioStart(requestId: requestId);
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
          autoGain: true,
          echoCancel: true,
          noiseSuppress: true,
        ),
      );
      _audioStreamSub = stream.listen(
        (chunk) {
          if (!mounted || _micPhase != _MicPhase.listening) return;
          _capturedBytes += chunk.length;
          widget.client.sendAudioFrame(Uint8List.fromList(chunk));
          _updateWaveformFromPcm(chunk);
        },
        onError: (_) {
          _micContinuous = false;
          _micUnlockedForQuestion = false;
          _activeMicRequestId = null;
          if (!mounted) return;
          unawaited(_stopMic(
            discard: true,
            silent: false,
            notifyServer: false,
          ));
          setState(() => _error = 'Microphone capture failed.');
        },
        cancelOnError: true,
      );

      _amplitudeSub = _recorder
          .onAmplitudeChanged(const Duration(milliseconds: 120))
          .listen((amp) {
        if (!mounted || _micPhase != _MicPhase.listening) return;
        final now = DateTime.now();
        // Manual mode has no server VAD: local amplitude decides when the
        // answer ended and flushes tutor playback when the student begins.
        final db = amp.current;
        if (db < _noiseFloorDb) {
          _noiseFloorDb = db;
        } else {
          _noiseFloorDb =
              math.min(_noiseFloorDb + 0.12, _speechDbThreshold - 5);
        }
        final speaking = db > math.max(_speechDbThreshold, _noiseFloorDb + 10);
        final firstSpeech = speaking && !_speechDetected;
        if (speaking) {
          _speechDetected = true;
          _lastVoiceAt = now;
        }
        if (firstSpeech) {
          unawaited(_audioPlayback.interrupt(keepNewer: true));
        }
        _evaluateVad(now);
      });

      _vadTimer?.cancel();
      _vadTimer = Timer.periodic(const Duration(milliseconds: 200), (_) {
        if (!mounted || _micPhase != _MicPhase.listening) return;
        _evaluateVad(DateTime.now());
      });
    } catch (e, st) {
      debugPrint('[LiveScreen] Microphone start failed: $e\n$st');
      _micContinuous = false;
      _micUnlockedForQuestion = false;
      _activeMicRequestId = null;
      await _stopMic(discard: true, silent: true, notifyServer: false);
      if (!mounted) return;
      setState(() {
        _micPhase = _MicPhase.idle;
        _error = 'Microphone access failed: $e';
      });
    }
  }

  void _updateWaveformFromPcm(Uint8List chunk) {
    if (chunk.length < 2) return;
    var sumSq = 0.0;
    var count = 0;
    final byteData = chunk.buffer.asByteData();
    for (var i = 0; i + 1 < chunk.length; i += 2) {
      final sample = byteData.getInt16(i, Endian.little);
      final norm = sample / 32768.0;
      sumSq += norm * norm;
      count++;
      if (count > 512) break;
    }
    if (count == 0) return;
    final rms = math.sqrt(sumSq / count);
    final noiseRms = math.pow(10.0, _noiseFloorDb / 20.0).toDouble();
    final level = ((rms - noiseRms) * 6).clamp(0.08, 1.0);
    if (!mounted || _micPhase != _MicPhase.listening) return;
    setState(() {
      _waveLevels.removeAt(0);
      _waveLevels.add(level);
    });
  }

  void _evaluateVad(DateTime now) {
    if (_micPhase != _MicPhase.listening || _submittingTurn) return;
    final started = _speechStartedAt;
    if (started == null) return;

    if (now.difference(started) >= _maxListeningDuration) {
      unawaited(_finishListening());
      return;
    }
    if (!_speechDetected) return;
    final last = _lastVoiceAt;
    if (last == null) return;
    if (now.difference(last) >= _silenceThreshold) {
      unawaited(_finishListening());
    }
  }

  Future<void> _finishListening({bool manualStop = false}) async {
    if (_micPhase != _MicPhase.listening || _submittingTurn) return;
    final minBytesForSubmit = manualStop ? 1600 : 3200; // 50ms manual, 100ms auto
    final hasAudio = _capturedBytes >= minBytesForSubmit;
    final shouldSubmit = hasAudio &&
        (_speechDetected || manualStop || _capturedBytes >= 8000);
    if (!shouldSubmit) {
      // Clear silence/noise from the buffer, but keep the teacher's question
      // open so the student can still answer.
      await _stopMic(discard: true, silent: true);
      if (mounted) setState(() => _micPhase = _MicPhase.idle);
      return;
    }
    _submittingTurn = true;
    if (mounted) {
      setState(() => _micPhase = _MicPhase.submitting);
    }
    try {
      await _stopMic(discard: false, silent: true);
      _micUnlockedForQuestion = false;
      _activeMicRequestId = null;
      if (mounted) {
        setState(() {
          _micPhase = _MicPhase.idle;
          _micContinuous = false;
        });
      }
    } finally {
      _submittingTurn = false;
    }
  }

  Future<void> _stopMic({
    required bool discard,
    required bool silent,
    bool notifyServer = true,
  }) async {
    _vadTimer?.cancel();
    _vadTimer = null;
    await _audioStreamSub?.cancel();
    _audioStreamSub = null;
    await _amplitudeSub?.cancel();
    _amplitudeSub = null;
    try {
      if (await _recorder.isRecording()) {
        await _recorder.stop();
      }
    } catch (e, st) {
      debugPrint('[LiveScreen] Failed to stop recorder: $e\n$st');
    }

    if (notifyServer &&
        (_micPhase == _MicPhase.listening ||
            _micPhase == _MicPhase.submitting)) {
      await widget.client.audioStop(
        submitted: !discard,
        requestId: _activeMicRequestId,
      );
    }

    if (mounted && !silent) {
      setState(() {
        if (_micPhase != _MicPhase.submitting) {
          _micPhase = _MicPhase.idle;
        }
        _speechDetected = false;
        _capturedBytes = 0;
        _speechStartedAt = null;
        _lastVoiceAt = null;
      });
    } else {
      _speechDetected = false;
      _capturedBytes = 0;
      _speechStartedAt = null;
      _lastVoiceAt = null;
      if (mounted && discard && _micPhase == _MicPhase.listening) {
        setState(() => _micPhase = _MicPhase.idle);
      }
    }
  }

  Future<void> _openCamera() async {
    if (_cameraOpening || _cameraActive || _imageSubmitting) return;
    if (_micPhase == _MicPhase.listening) {
      if (!_micContinuous) return;
      // Camera capture and the always-on mic must not fight over the device.
      _micContinuous = false;
      await _stopMic(discard: true, silent: true, notifyServer: false);
    }
    setState(() {
      _cameraOpening = true;
      _cameraError = null;
    });
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        throw StateError('No camera available on this device.');
      }
      final camera = cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );
      final controller = CameraController(
        camera,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.jpeg,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() {
        _cameraController = controller;
        _cameraOpening = false;
        _cameraActive = true;
      });
    } catch (e, st) {
      debugPrint('[LiveScreen] Camera open failed: $e\n$st');
      await _disposeCamera();
      if (!mounted) return;
      setState(() {
        _cameraOpening = false;
        _cameraActive = false;
        _cameraError = 'Camera is unavailable: $e';
      });
    }
  }

  Future<void> _disposeCamera() async {
    final controller = _cameraController;
    _cameraController = null;
    if (controller != null) {
      try {
        await controller.dispose();
      } catch (e, st) {
        debugPrint('[LiveScreen] Camera dispose failed: $e\n$st');
      }
    }
  }

  Future<void> _closeCamera() async {
    if (_imageSubmitting) return;
    await _disposeCamera();
    if (!mounted) return;
    setState(() {
      _cameraActive = false;
      _cameraOpening = false;
      _cameraError = null;
    });
  }

  Future<void> _captureImage() async {
    final controller = _cameraController;
    if (controller == null ||
        !controller.value.isInitialized ||
        _imageSubmitting) {
      return;
    }
    try {
      await controller.takePicture();
      if (!mounted) return;
      setState(() {
        _imageSubmitting = true;
      });
      // No existing app/realtime image-input mechanism is available in this build.
      await Future<void>.delayed(const Duration(milliseconds: 350));
      if (!mounted) return;
      setState(() => _imageSubmitting = false);
      await _closeCamera();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          behavior: SnackBarBehavior.floating,
          backgroundColor: AppColors.surfaceHigh,
          content: const Text(
            'Photo captured. Image sharing is not supported by the current lesson session.',
            style: TextStyle(color: AppColors.white),
          ),
        ),
      );
    } catch (e, st) {
      debugPrint('[LiveScreen] Image capture failed: $e\n$st');
      if (!mounted) return;
      setState(() {
        _imageSubmitting = false;
        _cameraError = 'Could not capture image: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // Report viewport size to the controller for mobile-first auto-pan decisions.
    final viewportSize = MediaQuery.of(context).size;
    if (_lastViewportSize != viewportSize) {
      _lastViewportSize = viewportSize;
      _viewportController.onViewportSizeChanged(viewportSize);
    }

    return Scaffold(
      backgroundColor: AppColors.background,
      body: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          // Blackboard remains mounted at all times.
          const Positioned.fill(child: ColoredBox(color: Color(0xFF000000))),
          Positioned.fill(
            child: GestureDetector(
              onPanStart: (_) => _viewportController.onManualInteractionStart(),
              onPanUpdate: (details) {
                final dxNorm = details.delta.dx / viewportSize.width;
                final dyNorm = details.delta.dy / viewportSize.height;
                _viewportController.setViewport(
                  _viewportController.offset + Offset(dxNorm, dyNorm),
                  _viewportController.scale,
                );
              },
              onPanEnd: (_) => _viewportController.onManualInteractionEnd(),
              onScaleStart: (_) =>
                  _viewportController.onManualInteractionStart(),
              onScaleUpdate: (details) {
                _viewportController.setViewport(
                  _viewportController.offset,
                  (_viewportController.scale * details.scale).clamp(0.5, 2.0),
                );
              },
              onScaleEnd: (_) => _viewportController.onManualInteractionEnd(),
              child: BlackboardView(
                controller: _boardController,
                viewportController: _viewportController,
              ),
            ),
          ),

          if (_error != null && !_connecting)
            Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: _InlineErrorStatus(
                  message: _error!,
                  onDismiss: () => setState(() => _error = null),
                  onRetry: _canRetryConnection ? _retryConnection : null,
                ),
              ),
            ),

          Positioned(
            top: MediaQuery.of(context).padding.top + 12,
            left: 12,
            child: _FloatingCircleButton(
              tooltip: 'Exit classroom',
              onPressed: _confirmExit,
              background: AppColors.surface.withValues(alpha: 0.45),
              borderColor: AppColors.border.withValues(alpha: 0.55),
              child: const PwonyIcon(
                PwonyIconKind.close,
                size: 18,
                color: AppColors.white,
              ),
            ),
          ),

          if (_connecting)
            Center(
              child: IgnorePointer(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      _connectionStatus,
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                        color: AppColors.white.withValues(alpha: 0.92),
                        letterSpacing: 0.2,
                      ),
                    ),
                    const SizedBox(height: 14),
                    SizedBox(
                      width: 220,
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(999),
                        child: LinearProgressIndicator(
                          value: _connectionProgress,
                          minHeight: 2,
                          backgroundColor:
                              AppColors.red.withValues(alpha: 0.18),
                          valueColor: const AlwaysStoppedAnimation<Color>(
                              AppColors.red),
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      _connectionDetail,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 12,
                        color: AppColors.white.withValues(alpha: 0.58),
                        height: 1.3,
                      ),
                    ),
                  ],
                ),
              ),
            ),

          if (!_connecting && _error == null && _waitingForTutor)
            Positioned(
              left: 24,
              right: 24,
              bottom: MediaQuery.of(context).padding.bottom + 88,
              child: IgnorePointer(
                child: Text(
                  _startupStatusMessage,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.white.withValues(alpha: 0.62),
                    height: 1.3,
                  ),
                ),
              ),
            ),

          Positioned(
            left: 0,
            right: 0,
            bottom: MediaQuery.of(context).padding.bottom + 16,
            child: IgnorePointer(
              ignoring: _cameraOpening,
              child: Center(
                child: _BottomControls(
                  paused: _paused,
                  micPhase: _micPhase,
                  micUnlocked: _micUnlockedForQuestion,
                  connecting: _connecting,
                  onPauseToggle: _togglePause,
                  onMicPressed: _onMicPressed,
                  onMicCancel: _cancelListening,
                  onCameraPressed: _openCamera,
                  onClearPressed: _onClearBoard,
                  pulse: _pulseController,
                  waveLevels: _waveLevels,
                ),
              ),
            ),
          ),

          if (_cameraOpening || _cameraActive)
            Positioned.fill(
              child: _CameraOverlay(
                controller: _cameraController,
                opening: _cameraOpening,
                submitting: _imageSubmitting,
                error: _cameraError,
                onClose: _closeCamera,
                onCapture: _captureImage,
              ),
            ),
        ],
      ),
    );
  }
}

class _InlineErrorStatus extends StatelessWidget {
  const _InlineErrorStatus({
    required this.message,
    required this.onDismiss,
    this.onRetry,
  });

  final String message;
  final VoidCallback onDismiss;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 520),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(
            message,
            textAlign: TextAlign.center,
            style: TextStyle(
              color: AppColors.white.withValues(alpha: 0.92),
              fontSize: 13,
              height: 1.4,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 10),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              if (onRetry != null)
                TextButton(
                  onPressed: onRetry,
                  style: TextButton.styleFrom(
                    foregroundColor: AppColors.red,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  child: const Text('Retry'),
                ),
              TextButton(
                onPressed: onDismiss,
                style: TextButton.styleFrom(
                  foregroundColor: AppColors.white.withValues(alpha: 0.72),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                ),
                child: const Text('Dismiss'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _FloatingCircleButton extends StatelessWidget {
  const _FloatingCircleButton({
    required this.child,
    required this.onPressed,
    required this.tooltip,
    required this.background,
    required this.borderColor,
    this.size = 40,
  });

  final Widget child;
  final VoidCallback? onPressed;
  final String tooltip;
  final Color background;
  final Color borderColor;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: ClipOval(
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: Material(
            color: Colors.transparent,
            child: InkWell(
              onTap: onPressed,
              customBorder: const CircleBorder(),
              child: Container(
                width: size,
                height: size,
                decoration: BoxDecoration(
                  color: background,
                  shape: BoxShape.circle,
                  border: Border.all(color: borderColor),
                ),
                alignment: Alignment.center,
                child: child,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _BottomControls extends StatelessWidget {
  const _BottomControls({
    required this.paused,
    required this.micPhase,
    required this.micUnlocked,
    required this.connecting,
    required this.onPauseToggle,
    required this.onMicPressed,
    required this.onMicCancel,
    required this.onCameraPressed,
    required this.onClearPressed,
    required this.pulse,
    required this.waveLevels,
  });

  final bool paused;
  final _MicPhase micPhase;
  final bool micUnlocked;
  final bool connecting;
  final VoidCallback onPauseToggle;
  final VoidCallback onMicPressed;
  final VoidCallback onMicCancel;
  final VoidCallback onCameraPressed;
  final VoidCallback onClearPressed;
  final Animation<double> pulse;
  final List<double> waveLevels;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        _CircleControl(
          tooltip: paused ? 'Resume' : 'Pause',
          onPressed: micPhase == _MicPhase.listening ? null : onPauseToggle,
          background: paused
              ? AppColors.red.withValues(alpha: 0.92)
              : AppColors.surface.withValues(alpha: 0.45),
          borderColor: paused
              ? AppColors.red.withValues(alpha: 0.7)
              : AppColors.border.withValues(alpha: 0.55),
          child: PwonyIcon(
            paused ? PwonyIconKind.play : PwonyIconKind.pause,
            size: 20,
            color: AppColors.white,
          ),
        ),
        const SizedBox(width: 12),
        _MicControl(
          phase: micPhase,
          enabled: !connecting && micUnlocked,
          onPressed: onMicPressed,
          onCancel: onMicCancel,
          pulse: pulse,
          waveLevels: waveLevels,
        ),
        const SizedBox(width: 12),
        _CircleControl(
          tooltip: 'Camera',
          onPressed: micPhase == _MicPhase.listening ? null : onCameraPressed,
          background: AppColors.surface.withValues(alpha: 0.45),
          borderColor: AppColors.border.withValues(alpha: 0.55),
          child: const PwonyIcon(
            PwonyIconKind.camera,
            size: 20,
            color: AppColors.white,
          ),
        ),
        const SizedBox(width: 12),
        _CircleControl(
          tooltip: 'Clear board',
          onPressed: micPhase == _MicPhase.listening ? null : onClearPressed,
          background: AppColors.surface.withValues(alpha: 0.45),
          borderColor: AppColors.border.withValues(alpha: 0.55),
          child: const PwonyIcon(
            PwonyIconKind.delete,
            size: 20,
            color: AppColors.white,
          ),
        ),
      ],
    );
  }
}

class _CircleControl extends StatelessWidget {
  const _CircleControl({
    required this.tooltip,
    required this.onPressed,
    required this.child,
    required this.background,
    required this.borderColor,
  });

  final String tooltip;
  final VoidCallback? onPressed;
  final Widget child;
  final Color background;
  final Color borderColor;

  @override
  Widget build(BuildContext context) {
    final disabled = onPressed == null;
    return Semantics(
      button: true,
      label: tooltip,
      child: Tooltip(
        message: tooltip,
        child: ClipOval(
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOutCubic,
              width: 52,
              height: 52,
              decoration: BoxDecoration(
                color: background,
                shape: BoxShape.circle,
                border: Border.all(color: borderColor),
              ),
              child: Material(
                color: Colors.transparent,
                child: InkWell(
                  onTap: onPressed,
                  customBorder: const CircleBorder(),
                  child: Center(
                    child: Opacity(opacity: disabled ? 0.45 : 1, child: child),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _MicControl extends StatelessWidget {
  const _MicControl({
    required this.phase,
    required this.enabled,
    required this.onPressed,
    required this.onCancel,
    required this.pulse,
    required this.waveLevels,
  });

  final _MicPhase phase;
  final bool enabled;
  final VoidCallback onPressed;
  final VoidCallback onCancel;
  final Animation<double> pulse;
  final List<double> waveLevels;

  @override
  Widget build(BuildContext context) {
    final listening = phase == _MicPhase.listening;
    final submitting = phase == _MicPhase.submitting;
    final bg = listening
        ? AppColors.red.withValues(alpha: 0.95)
        : submitting
            ? AppColors.red.withValues(alpha: 0.75)
            : AppColors.red.withValues(alpha: 0.92);

    return Semantics(
      button: true,
      label: listening
          ? 'Answering, tap to send'
          : enabled
              ? 'Tap to answer'
              : 'Waiting for a question',
      child: Tooltip(
        message: listening
            ? 'Listening for your answer… tap to send, long press to cancel'
            : enabled
                ? 'Your answer is requested — tap to speak'
                : 'The microphone unlocks only when the tutor asks for an answer',
        child: ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOutCubic,
              width: 132,
              height: 52,
              decoration: BoxDecoration(
                color: bg,
                borderRadius: BorderRadius.circular(999),
                border: Border.all(
                  color: AppColors.white
                      .withValues(alpha: listening ? 0.12 : 0.08),
                ),
              ),
              child: Material(
                color: Colors.transparent,
                child: InkWell(
                  onTap: enabled ? onPressed : null,
                  onLongPress: enabled && listening ? onCancel : null,
                  borderRadius: BorderRadius.circular(999),
                  child: Center(
                    child: AnimatedSwitcher(
                      duration: const Duration(milliseconds: 180),
                      switchInCurve: Curves.easeOutCubic,
                      switchOutCurve: Curves.easeInCubic,
                      child: listening
                          ? _Waveform(
                              key: const ValueKey<String>('wave'),
                              levels: waveLevels,
                              pulse: pulse,
                            )
                          : submitting
                              ? SizedBox(
                                  key: const ValueKey<String>('submitting'),
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color:
                                        AppColors.white.withValues(alpha: 0.95),
                                  ),
                                )
                              : const PwonyIcon(
                                  key: ValueKey<String>('mic'),
                                  PwonyIconKind.microphone,
                                  size: 22,
                                  color: AppColors.white,
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
  }
}

class _Waveform extends StatelessWidget {
  const _Waveform({super.key, required this.levels, required this.pulse});

  final List<double> levels;
  final Animation<double> pulse;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: pulse,
      builder: (context, _) {
        return SizedBox(
          width: 84,
          height: 22,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: List<Widget>.generate(levels.length, (i) {
              final base = levels[i];
              final wobble = 0.92 + (pulse.value * 0.16);
              final h = (4 + (18 * base * wobble)).clamp(4.0, 22.0);
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 1.4),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 90),
                  curve: Curves.easeOut,
                  width: 2.6,
                  height: h,
                  decoration: BoxDecoration(
                    color: AppColors.white.withValues(alpha: 0.92),
                    borderRadius: BorderRadius.circular(99),
                  ),
                ),
              );
            }),
          ),
        );
      },
    );
  }
}

class _CameraOverlay extends StatelessWidget {
  const _CameraOverlay({
    required this.controller,
    required this.opening,
    required this.submitting,
    required this.error,
    required this.onClose,
    required this.onCapture,
  });

  final CameraController? controller;
  final bool opening;
  final bool submitting;
  final String? error;
  final VoidCallback onClose;
  final VoidCallback onCapture;

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final bottomInset = media.padding.bottom;
    final panelHeight =
        (media.size.height * 0.52).clamp(320.0, media.size.height * 0.7);

    return Stack(
      children: <Widget>[
        Positioned.fill(
          child: GestureDetector(
            onTap: submitting ? null : onClose,
            behavior: HitTestBehavior.opaque,
            child: Container(color: Colors.black.withValues(alpha: 0.28)),
          ),
        ),
        AnimatedPositioned(
          duration: const Duration(milliseconds: 320),
          curve: Curves.easeOutCubic,
          left: 0,
          right: 0,
          bottom: 0,
          height: panelHeight,
          child: ClipRRect(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
              child: Container(
                decoration: BoxDecoration(
                  color: AppColors.surface.withValues(alpha: 0.72),
                  borderRadius:
                      const BorderRadius.vertical(top: Radius.circular(28)),
                  border: Border.all(
                    color: AppColors.border.withValues(alpha: 0.55),
                  ),
                ),
                child: SafeArea(
                  top: false,
                  child: Padding(
                    padding:
                        EdgeInsets.fromLTRB(16, 12, 16, 12 + bottomInset * 0.2),
                    child: Column(
                      children: <Widget>[
                        Container(
                          width: 36,
                          height: 4,
                          decoration: BoxDecoration(
                            color: AppColors.border.withValues(alpha: 0.7),
                            borderRadius: BorderRadius.circular(99),
                          ),
                        ),
                        const SizedBox(height: 10),
                        Row(
                          children: <Widget>[
                            const Expanded(
                              child: Text(
                                'Camera',
                                style: TextStyle(
                                  color: AppColors.white,
                                  fontSize: 15,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                            _FloatingCircleButton(
                              tooltip: 'Close camera',
                              onPressed: submitting ? null : onClose,
                              size: 34,
                              background:
                                  AppColors.surface.withValues(alpha: 0.55),
                              borderColor:
                                  AppColors.border.withValues(alpha: 0.55),
                              child: const PwonyIcon(
                                PwonyIconKind.close,
                                size: 16,
                                color: AppColors.white,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 12),
                        Expanded(
                          child: ClipRRect(
                            borderRadius: BorderRadius.circular(18),
                            child: Container(
                              color: Colors.black,
                              child: _buildPreview(),
                            ),
                          ),
                        ),
                        const SizedBox(height: 14),
                        if (error != null)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: Text(
                              error!,
                              style: const TextStyle(
                                color: AppColors.muted,
                                fontSize: 12,
                              ),
                            ),
                          ),
                        SizedBox(
                          height: 64,
                          child: Center(
                            child: GestureDetector(
                              onTap: submitting || opening || controller == null
                                  ? null
                                  : onCapture,
                              child: AnimatedContainer(
                                duration: const Duration(milliseconds: 180),
                                width: 64,
                                height: 64,
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: AppColors.white,
                                  border: Border.all(
                                    color:
                                        AppColors.white.withValues(alpha: 0.35),
                                    width: 4,
                                  ),
                                ),
                                child: submitting
                                    ? const Padding(
                                        padding: EdgeInsets.all(18),
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2.4,
                                          color: AppColors.background,
                                        ),
                                      )
                                    : const SizedBox.shrink(),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildPreview() {
    if (opening) {
      return const Center(
        child:
            CircularProgressIndicator(strokeWidth: 2, color: AppColors.white),
      );
    }
    final c = controller;
    if (c == null || !c.value.isInitialized) {
      return const Center(
        child: Text(
          'Camera preview unavailable',
          style: TextStyle(color: AppColors.muted),
        ),
      );
    }
    final preview = CameraPreview(c);
    if (submitting) {
      return Stack(
        fit: StackFit.expand,
        children: <Widget>[
          preview,
          Container(color: Colors.black.withValues(alpha: 0.25)),
          const Center(
            child: Text(
              'Processing…',
              style: TextStyle(color: AppColors.white, fontSize: 13),
            ),
          ),
        ],
      );
    }
    return preview;
  }
}
