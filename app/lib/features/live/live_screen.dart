import 'dart:async';

import 'package:flutter/material.dart' hide ConnectionState;
import 'package:record/record.dart';

import '../../blackboard/blackboard_view.dart';
import '../../blackboard/model/board_document.dart';
import '../../blackboard/model/board_event.dart';
import '../../blackboard/runtime/blackboard_controller.dart';
import '../../core/formatters.dart';
import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../services/realtime_client.dart';

/// Live classroom screen: premium realtime teaching surface.
class LiveScreen extends StatefulWidget {
  const LiveScreen({super.key, required this.client});

  final RealtimeClient client;

  @override
  State<LiveScreen> createState() => _LiveScreenState();
}

class _LiveScreenState extends State<LiveScreen> {
  String? _lessonId;
  bool _micOpen = false;
  bool _connecting = false;
  String? _error;
  Map<String, dynamic>? _lessonDetail;

  final AudioRecorder _recorder = AudioRecorder();
  final List<Map<String, dynamic>> _transcript = [];
  bool _showTranscript = false;

  late final BlackboardController _boardController;

  StreamSubscription<ConnectionState>? _connSub;
  StreamSubscription<String>? _errSub;
  StreamSubscription<RealtimeMessage>? _msgSub;

  @override
  void initState() {
    super.initState();
    _boardController = BlackboardController();
    _lessonId = widget.client.lessonId;
    _connecting = widget.client.state == ConnectionState.connecting;

    _connSub = widget.client.connectionState.listen((state) {
      if (!mounted) return;
      setState(() {
        _lessonId ??= widget.client.lessonId;
        switch (state) {
          case ConnectionState.connected:
            _connecting = false;
            _error = null;
          case ConnectionState.connecting:
            _connecting = true;
          case ConnectionState.interrupted:
            _connecting = false;
            _error = 'Another device took over this lesson.';
          case ConnectionState.error:
            _connecting = false;
          case ConnectionState.disconnected:
            _connecting = false;
        }
      });
    });
    _errSub = widget.client.errors.listen((message) {
      if (!mounted) return;
      setState(() => _error = message);
    });
    _msgSub = widget.client.messages.listen(_handleMessage);
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
      case RealtimeMessageType.transcriptEntry:
        if (!mounted) return;
        setState(() {
          final entry = message.payload['entry'] ?? message.payload;
          if (entry is Map) {
            _transcript.insert(0, Map<String, dynamic>.from(entry));
          }
        });
        break;
      case RealtimeMessageType.lessonState:
        if (!mounted) return;
        setState(() {
          final lesson = message.payload['lesson'];
          if (lesson is Map<String, dynamic>) {
            _lessonDetail = lesson;
          } else if (lesson is Map) {
            _lessonDetail = Map<String, dynamic>.from(lesson);
          }
        });
        break;
      case RealtimeMessageType.sessionEnded:
        if (!mounted) return;
        setState(() => _error = 'Lesson ended.');
        break;
      default:
        break;
    }
  }

  void _applySessionReady(Map<String, dynamic> payload) {
    final lesson = payload['lesson'];
    if (lesson is Map) {
      setState(() => _lessonDetail = Map<String, dynamic>.from(lesson));
    }
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
    } catch (_) {
      // Keep board stable on malformed snapshots.
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

  @override
  void dispose() {
    unawaited(_connSub?.cancel());
    unawaited(_errSub?.cancel());
    unawaited(_msgSub?.cancel());
    unawaited(_stopMic());
    _boardController.dispose();
    super.dispose();
  }

  Future<void> _toggleMic() async {
    if (_micOpen) {
      await _stopMic();
    } else {
      await _startMic();
    }
  }

  Future<void> _startMic() async {
    if (_lessonId == null) return;
    try {
      await _recorder.start(const RecordConfig(), path: 'classroom.m4a');
      setState(() => _micOpen = true);
      unawaited(widget.client.audioStart());
      _captureLoop();
    } catch (_) {
      setState(() => _error = 'Microphone access failed.');
    }
  }

  Future<void> _stopMic() async {
    try {
      await _recorder.stop();
    } catch (_) {}
    if (mounted) setState(() => _micOpen = false);
    await widget.client.audioStop();
  }

  void _captureLoop() async {
    while (_micOpen && mounted) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (!_micOpen) break;
    }
  }

  Future<void> _endLesson() async {
    await widget.client.endSession();
    if (!mounted) return;
    Navigator.of(context).maybePop(true);
  }

  String get _topic {
    final t = _lessonDetail?['topic'] as String?;
    if (t != null && t.trim().isNotEmpty) return t.trim();
    return 'Live Tutorial';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: <Widget>[
            _buildHeader(context),
            if (_error != null) _buildErrorBanner(),
            Expanded(child: _buildBody(context)),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      child: Row(
        children: <Widget>[
          GlassIconButton(
            icon: ClasrumIconKind.back,
            tooltip: 'Back',
            size: 40,
            iconSize: 18,
            blurSigma: 8,
            backgroundOpacity: 0.4,
            onPressed: () => Navigator.of(context).maybePop(),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              _topic,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                  ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildErrorBanner() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: GlassContainer(
        radius: 12,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        blurSigma: 6,
        backgroundOpacity: 0.35,
        borderColor: AppColors.red.withValues(alpha: 0.5),
        child: Row(
          children: <Widget>[
            const ClasrumIcon(ClasrumIconKind.info, size: 16, color: AppColors.muted),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                _error!,
                style: const TextStyle(color: AppColors.muted, fontSize: 12),
              ),
            ),
            GestureDetector(
              onTap: () => setState(() => _error = null),
              child: const ClasrumIcon(ClasrumIconKind.close, size: 16, color: AppColors.muted),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_connecting && _lessonId == null) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (_lessonId == null) {
      return const Center(
        child: Text(
          'Select a lesson from Home to start.',
          style: TextStyle(color: AppColors.muted),
        ),
      );
    }
    return Stack(
      children: <Widget>[
        // Blackboard — main focus
        Positioned.fill(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 88),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(18),
              child: Container(
                color: const Color(0xFF171817),
                child: BlackboardView(controller: _boardController),
              ),
            ),
          ),
        ),
        // Subtle transcript toggle + panel
        Positioned(
          right: 16,
          top: 8,
          child: _TranscriptPanel(
            expanded: _showTranscript,
            entries: _transcript,
            onToggle: () => setState(() => _showTranscript = !_showTranscript),
          ),
        ),
        // Minimal controls
        Positioned(
          left: 12,
          right: 12,
          bottom: 12,
          child: _ControlBar(
            micOpen: _micOpen,
            connecting: _connecting,
            onToggleMic: _toggleMic,
            onInterrupt: () => widget.client.interrupt(),
            onEnd: _endLesson,
          ),
        ),
      ],
    );
  }
}

class _ControlBar extends StatelessWidget {
  const _ControlBar({
    required this.micOpen,
    required this.connecting,
    required this.onToggleMic,
    required this.onInterrupt,
    required this.onEnd,
  });

  final bool micOpen;
  final bool connecting;
  final VoidCallback onToggleMic;
  final VoidCallback onInterrupt;
  final VoidCallback onEnd;

  @override
  Widget build(BuildContext context) {
    return GlassContainer(
      radius: 20,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      blurSigma: 12,
      backgroundOpacity: 0.45,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: <Widget>[
          _ControlIcon(
            icon: micOpen ? ClasrumIconKind.microphone : ClasrumIconKind.pause,
            tooltip: micOpen ? 'Listening' : 'Speak',
            active: micOpen,
            onPressed: connecting ? null : onToggleMic,
          ),
          const SizedBox(width: 8),
          _ControlIcon(
            icon: ClasrumIconKind.interrupt,
            tooltip: 'Interrupt',
            onPressed: connecting ? null : onInterrupt,
          ),
          const SizedBox(width: 8),
          _ControlIcon(
            icon: ClasrumIconKind.endLesson,
            tooltip: 'End lesson',
            onPressed: connecting ? null : onEnd,
            danger: true,
          ),
        ],
      ),
    );
  }
}

class _ControlIcon extends StatelessWidget {
  const _ControlIcon({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.active = false,
    this.danger = false,
  });

  final ClasrumIconKind icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool active;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final bg = active ? AppColors.blue : Colors.transparent;
    final fg = active
        ? AppColors.white
        : (danger ? AppColors.muted : AppColors.white);
    return Tooltip(
      message: tooltip,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(14),
          child: InkWell(
            onTap: onPressed,
            borderRadius: BorderRadius.circular(14),
            child: SizedBox(
              width: 44,
              height: 44,
              child: Center(
                child: AnimatedScale(
                  duration: const Duration(milliseconds: 200),
                  scale: active ? 1.0 : 0.98,
                  child: ClasrumIcon(
                    icon,
                    size: 20,
                    color: fg.withValues(alpha: onPressed == null ? 0.4 : 1),
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

class _TranscriptPanel extends StatelessWidget {
  const _TranscriptPanel({
    required this.expanded,
    required this.entries,
    required this.onToggle,
  });

  final bool expanded;
  final List<Map<String, dynamic>> entries;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    if (!expanded) {
      return GlassIconButton(
        icon: ClasrumIconKind.transcript,
        tooltip: 'Transcript',
        size: 36,
        iconSize: 18,
        onPressed: onToggle,
        blurSigma: 8,
        backgroundOpacity: 0.35,
      );
    }
    return SizedBox(
      width: 260,
      height: 220,
      child: GlassContainer(
        radius: 16,
        padding: const EdgeInsets.all(10),
        blurSigma: 10,
        backgroundOpacity: 0.55,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Expanded(
                  child: Text(
                    'Transcript',
                    style: TextStyle(
                      color: AppColors.white,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                InkWell(
                  onTap: onToggle,
                  borderRadius: BorderRadius.circular(12),
                  child: const Padding(
                    padding: EdgeInsets.all(4),
                    child: ClasrumIcon(ClasrumIconKind.close, size: 16, color: AppColors.muted),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Expanded(
              child: entries.isEmpty
                  ? const Center(
                      child: Text(
                        'Transcript appears here.',
                        style:
                            TextStyle(color: AppColors.muted, fontSize: 12),
                      ),
                    )
                  : ListView.builder(
                      reverse: false,
                      itemCount: entries.length,
                      itemBuilder: (context, index) {
                        final entry = entries[index];
                        final speaker = entry['speaker'] as String? ?? 'system';
                        final text = entry['text'] as String? ?? '';
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: <Widget>[
                              Text(
                                speakerLabel(speaker),
                                style: const TextStyle(
                                  color: AppColors.muted,
                                  fontSize: 10,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                text,
                                style: const TextStyle(
                                  color: AppColors.white,
                                  fontSize: 12,
                                  height: 1.4,
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
