import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart' hide ConnectionState;
import 'package:record/record.dart';

import '../../blackboard/blackboard_view.dart';
import '../../blackboard/model/board_document.dart';
import '../../blackboard/model/board_event.dart';
import '../../blackboard/model/board_geometry.dart';
import '../../blackboard/runtime/blackboard_controller.dart';
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
  Timer? _connectProgressTimer;

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _audioStreamSub;
  StreamSubscription<Amplitude>? _amplitudeSub;
  bool _speechDetected = false;
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
  late final AnimationController _pulseController;
  late final AiAudioPlaybackController _audioPlayback;

  Size? _lastViewportSize;

  StreamSubscription<ConnectionState>? _connSub;
  StreamSubscription<String>? _errSub;
  StreamSubscription<RealtimeMessage>? _msgSub;

  static const Duration _silenceThreshold = Duration(milliseconds: 1200);
  static const Duration _maxListeningDuration = Duration(seconds: 20);
  static const double _speechDbThreshold = -38;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _boardController = BlackboardController();
    _viewportController = BoardViewportController(tickerProvider: this);
    // Track board content bounds so the viewport can clamp panning.
    _boardController.addListener(() {
      if (!mounted) return;
      final bounds = _boardController.document.allVisibleBounds;
      _viewportController.onContentBoundsChanged(bounds);
    });
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
      setState(() => _error = message);
    });
    _msgSub = widget.client.messages.listen(_handleMessage);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _vadTimer?.cancel();
    _connectProgressTimer?.cancel();
    unawaited(_connSub?.cancel());
    unawaited(_errSub?.cancel());
    unawaited(_msgSub?.cancel());
    unawaited(_stopMic(discard: true, silent: true));
    unawaited(_disposeCamera());
    unawaited(_audioPlayback.reset(endState: AiPlaybackState.idle));
    _audioPlayback.dispose();
    _pulseController.dispose();
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
        unawaited(_stopMic(discard: true, silent: true));
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

  void _syncConnectionUi(ConnectionState state) {
    switch (state) {
      case ConnectionState.connecting:
        _connecting = true;
        _connectionStatus = 'Connecting to Qwen model...';
        _connectionDetail =
            'Establishing the realtime voice session. This may take a few seconds.';
        if (_connectionProgress == null || _connectionProgress! <= 0.0) {
          _connectionProgress = 0.15;
        }
        _connectProgressTimer ??=
            Timer.periodic(const Duration(milliseconds: 350), (_) {
          if (!mounted) return;
          setState(() {
            final current = _connectionProgress ?? 0.15;
            // Ease toward 90% while waiting for session.ready.
            _connectionProgress = current + ((0.9 - current) * 0.12);
          });
        });
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
      case ConnectionState.error:
      case ConnectionState.disconnected:
        _connecting = false;
        _connectProgressTimer?.cancel();
        _connectProgressTimer = null;
        _connectionProgress = 0.0;
        break;
    }
  }

  void _handleMessage(RealtimeMessage message) {
    if (!mounted) return;
    setState(() {
      _lessonId ??= widget.client.lessonId ?? message.lessonId;
    });
    switch (message.type) {
      case RealtimeMessageType.sessionReady:
        _applySessionReady(message.payload);
        break;
      case RealtimeMessageType.boardSnapshot:
        _applyBoardSnapshot(message.payload);
        break;
      case RealtimeMessageType.boardEvent:
        _applyBoardEvent(message.payload);
        break;
      case RealtimeMessageType.boardEvents:
        _applyBoardEvents(message.payload);
        break;
      case RealtimeMessageType.lessonState:
        // Lesson metadata updates are ignored by the minimal classroom UI.
        break;
      case RealtimeMessageType.teacherState:
        // Teacher state is reflected implicitly by streamed audio/playback.
        break;
      case RealtimeMessageType.audioDelta:
        _handleAudioDelta(message);
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
      case RealtimeMessageType.sessionEnded:
        unawaited(_audioPlayback.reset(endState: AiPlaybackState.completed));
        setState(() {
          _error = 'Lesson ended.';
        });
        break;
      case RealtimeMessageType.viewportFocus:
        _applyViewportFocus(message.payload);
        break;
      case RealtimeMessageType.sessionInterrupted:
        unawaited(_audioPlayback.reset(endState: AiPlaybackState.interrupted));
        unawaited(_stopMic(discard: true, silent: true));
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
      _boardController.enqueue(event);
    } catch (_) {}
  }

  void _applyBoardEvents(Map<String, dynamic> payload) {
    final raw = payload['events'] ?? payload['board_events'];
    if (raw is! List) return;
    final events = <BoardEvent>[];
    for (final item in raw) {
      if (item is! Map) continue;
      try {
        events.add(BoardEvent.fromJson(Map<String, dynamic>.from(item)));
      } catch (_) {}
    }
    if (events.isNotEmpty) _boardController.enqueueAll(events);
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
    // During student mic turn, ignore incoming tutor audio for the interrupted turn.
    if (_micPhase != _MicPhase.idle) return;
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
    } catch (_) {
      // Malformed frames/playback errors must not crash the classroom.
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
      await _stopMic(discard: true, silent: true);
      await _audioPlayback.reset(endState: AiPlaybackState.completed);
      await widget.client.disconnect();
      if (mounted) Navigator.of(context).maybePop(true);
    }
  }

  Future<void> _togglePause() async {
    if (_micPhase == _MicPhase.listening || _submittingTurn) return;
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
    if (_connecting || _submittingTurn) return;
    if (_micPhase == _MicPhase.listening) {
      await _stopMic(discard: true, silent: false);
      return;
    }
    await _startListening();
  }

  Future<void> _startListening() async {
    if (_lessonId == null || _micPhase != _MicPhase.idle || _submittingTurn) {
      return;
    }
    if (_cameraActive || _cameraOpening) return;

    try {
      final hasPermission = await _recorder.hasPermission();
      if (!hasPermission) {
        if (!mounted) return;
        setState(() => _error = 'Microphone permission is required to speak.');
        return;
      }

      await widget.client.interrupt();
      await _audioPlayback.interrupt(keepNewer: true);
      setState(() {
        _micPhase = _MicPhase.listening;
        _speechDetected = false;
        _capturedBytes = 0;
        _speechStartedAt = DateTime.now();
        _lastVoiceAt = null;
        _error = null;
        _paused = false;
        for (var i = 0; i < _waveLevels.length; i++) {
          _waveLevels[i] = 0.08;
        }
      });

      await widget.client.audioStart();
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
          if (!mounted) return;
          unawaited(_stopMic(discard: true, silent: false));
          setState(() => _error = 'Microphone capture failed.');
        },
        cancelOnError: true,
      );

      _amplitudeSub = _recorder
          .onAmplitudeChanged(const Duration(milliseconds: 120))
          .listen((amp) {
        if (!mounted || _micPhase != _MicPhase.listening) return;
        final now = DateTime.now();
        final speaking = amp.current > _speechDbThreshold;
        if (speaking) {
          _speechDetected = true;
          _lastVoiceAt = now;
        }
        _evaluateVad(now);
      });

      _vadTimer?.cancel();
      _vadTimer = Timer.periodic(const Duration(milliseconds: 200), (_) {
        if (!mounted || _micPhase != _MicPhase.listening) return;
        _evaluateVad(DateTime.now());
      });
    } catch (_) {
      await _stopMic(discard: true, silent: true);
      if (!mounted) return;
      setState(() {
        _micPhase = _MicPhase.idle;
        _error = 'Microphone access failed.';
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
    final level = (rms * 6).clamp(0.08, 1.0);
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

  Future<void> _finishListening() async {
    if (_micPhase != _MicPhase.listening || _submittingTurn) return;
    if (!_speechDetected || _capturedBytes < 800) {
      await _stopMic(discard: true, silent: false);
      return;
    }
    _submittingTurn = true;
    if (mounted) {
      setState(() => _micPhase = _MicPhase.submitting);
    }
    try {
      await _stopMic(discard: false, silent: true);
      if (mounted) {
        setState(() {
          _micPhase = _MicPhase.idle;
        });
      }
    } finally {
      _submittingTurn = false;
    }
  }

  Future<void> _stopMic({required bool discard, required bool silent}) async {
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
    } catch (_) {}

    if (_micPhase == _MicPhase.listening || _micPhase == _MicPhase.submitting) {
      if (!discard) {
        await widget.client.audioStop();
      }
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
    if (_micPhase == _MicPhase.listening) return;
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
    } catch (_) {
      await _disposeCamera();
      if (!mounted) return;
      setState(() {
        _cameraOpening = false;
        _cameraActive = false;
        _cameraError = 'Camera is unavailable.';
      });
    }
  }

  Future<void> _disposeCamera() async {
    final controller = _cameraController;
    _cameraController = null;
    if (controller != null) {
      try {
        await controller.dispose();
      } catch (_) {}
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
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _imageSubmitting = false;
        _cameraError = 'Could not capture image.';
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

          if (_error != null)
            Positioned(
              left: 16,
              right: 16,
              top: MediaQuery.of(context).padding.top + 64,
              child: _TopErrorBanner(
                message: _error!,
                onDismiss: () => setState(() => _error = null),
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
            Positioned(
              top: MediaQuery.of(context).padding.top + 64,
              left: 24,
              right: 24,
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 420),
                  child: GlassContainer(
                    radius: 16,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                    blurSigma: 10,
                    backgroundOpacity: 0.55,
                    borderColor: AppColors.border.withValues(alpha: 0.6),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Row(
                          children: <Widget>[
                            const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2.2,
                                color: AppColors.white,
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Text(
                                _connectionStatus,
                                style: const TextStyle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.w600,
                                  color: AppColors.white,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(999),
                          child: LinearProgressIndicator(
                            value: _connectionProgress,
                            minHeight: 4,
                            backgroundColor: const Color(0x33FFFFFF),
                            valueColor:
                                const AlwaysStoppedAnimation<Color>(AppColors.white),
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          _connectionDetail,
                          style: TextStyle(
                            fontSize: 12,
                            color: AppColors.white.withValues(alpha: 0.72),
                            height: 1.3,
                          ),
                        ),
                      ],
                    ),
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
                  connecting: _connecting,
                  onPauseToggle: _togglePause,
                  onMicPressed: _onMicPressed,
                  onCameraPressed: _openCamera,
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

class _TopErrorBanner extends StatelessWidget {
  const _TopErrorBanner({required this.message, required this.onDismiss});

  final String message;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return GlassContainer(
      radius: 14,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      blurSigma: 10,
      backgroundOpacity: 0.45,
      borderColor: AppColors.red.withValues(alpha: 0.45),
      child: Row(
        children: <Widget>[
          const PwonyIcon(PwonyIconKind.info, size: 16, color: AppColors.muted),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                  color: AppColors.white, fontSize: 12, height: 1.3),
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: onDismiss,
            behavior: HitTestBehavior.opaque,
            child: const Padding(
              padding: EdgeInsets.all(4),
              child: PwonyIcon(PwonyIconKind.close,
                  size: 16, color: AppColors.muted),
            ),
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
    required this.connecting,
    required this.onPauseToggle,
    required this.onMicPressed,
    required this.onCameraPressed,
    required this.pulse,
    required this.waveLevels,
  });

  final bool paused;
  final _MicPhase micPhase;
  final bool connecting;
  final VoidCallback onPauseToggle;
  final VoidCallback onMicPressed;
  final VoidCallback onCameraPressed;
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
          enabled: !connecting,
          onPressed: onMicPressed,
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
    required this.pulse,
    required this.waveLevels,
  });

  final _MicPhase phase;
  final bool enabled;
  final VoidCallback onPressed;
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
      label: listening ? 'Listening' : 'Speak',
      child: Tooltip(
        message: listening ? 'Listening… tap to cancel' : 'Tap to speak',
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
