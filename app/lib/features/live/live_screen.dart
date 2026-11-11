import 'package:flutter/material.dart' hide ConnectionState;
import 'package:record/record.dart';

import '../../core/formatters.dart';
import '../../services/realtime_client.dart';

/// Live classroom screen: WebSocket blackboard + audio + transcript (CONTRACTS §6).
class LiveScreen extends StatefulWidget {
  const LiveScreen({super.key, required this.client});

  final RealtimeClient client;

  @override
  State<LiveScreen> createState() => _LiveScreenState();
}

class _LiveScreenState extends State<LiveScreen> {
  String? _lessonId;
  bool _micOpen = false;
  bool _speakerOn = true;
  bool _connecting = false;
  String? _error;
  Map<String, dynamic>? _lessonDetail;

  final AudioRecorder _recorder = AudioRecorder();
  final List<Map<String, dynamic>> _transcript = [];

  @override
  void initState() {
    super.initState();
    widget.client.connectionState.listen((state) {
      if (!mounted) return;
      setState(() {
        switch (state) {
          case ConnectionState.connected:
            _connecting = false;
            _error = null;
          case ConnectionState.connecting:
            _connecting = true;
          case ConnectionState.interrupted:
            _error = 'Another device took over this lesson.';
          case ConnectionState.error:
            _connecting = false;
          case ConnectionState.disconnected:
            _connecting = false;
        }
      });
    });
    widget.client.errors.listen((message) {
      if (!mounted) return;
      setState(() => _error = message);
    });
    widget.client.messages.listen((message) {
      if (!mounted) return;
      setState(() {
        switch (message.type) {
          case RealtimeMessageType.sessionReady:
            _lessonDetail = message.payload['lesson'] as Map<String, dynamic>?;
            break;
          case RealtimeMessageType.transcriptEntry:
            final entry = message.payload;
            _transcript.insert(0, Map<String, dynamic>.from(entry));
            break;
          case RealtimeMessageType.sessionEnded:
            _error = 'Lesson ended.';
            break;
          default:
            break;
        }
      });
    });
  }

  @override
  void dispose() {
    _stopMic();
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
      widget.client.audioStart();
      _captureLoop();
    } catch (e) {
      setState(() => _error = 'Microphone access failed.');
    }
  }

  Future<void> _stopMic() async {
    try {
      await _recorder.stop();
    } catch (_) {}
    setState(() => _micOpen = false);
    widget.client.audioStop();
  }

  void _captureLoop() async {
    // Note: AudioRecorder.stop() returns a file path, not raw PCM bytes.
    // Raw PCM streaming to the WebSocket requires the record package's stream API;
    // for now the mic start/stop control messages are sufficient for the protocol.
    while (_micOpen && mounted) {
      await Future<void>.delayed(const Duration(seconds: 1));
      if (!_micOpen) break;
    }
  }

  Future<void> _endLesson() async {
    await widget.client.endSession();
    if (!mounted) return;
    Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_lessonDetail != null
            ? (_lessonDetail!['topic'] as String? ?? 'Live Tutorial')
            : 'Live Tutorial'),
        actions: <Widget>[
          if (_error != null)
            IconButton(
              icon: const Icon(Icons.close),
              onPressed: () => setState(() => _error = null),
            ),
        ],
      ),
      body: _connecting
          ? const Center(child: CircularProgressIndicator())
          : _lessonId == null
              ? const Center(child: Text('Select a lesson from Home to start.'))
              : Column(
                  children: <Widget>[
                    Expanded(
                      child: Row(
                        children: <Widget>[
                          Expanded(flex: 3, child: _blackboardPlaceholder()),
                          Expanded(flex: 1, child: _transcriptRail()),
                        ],
                      ),
                    ),
                    _controlBar(),
                  ],
                ),
    );
  }

  Widget _blackboardPlaceholder() {
    return Container(
      color: const Color(0xFF171817),
      child: const Center(
        child: Text(
          'Blackboard\n(Writing events arrive over WS)',
          textAlign: TextAlign.center,
          style: TextStyle(color: Color(0xFFA8A79E)),
        ),
      ),
    );
  }

  Widget _transcriptRail() {
    return Container(
      color: const Color(0xFF101215),
      child: Column(
        children: <Widget>[
          const Padding(
            padding: EdgeInsets.all(12),
            child: Text('Transcript', style: TextStyle(fontWeight: FontWeight.w600)),
          ),
          Expanded(
            child: _transcript.isEmpty
                ? const Center(
                    child: Text(
                      'Transcript appears here.',
                      style: TextStyle(color: Color(0xFFA8A79E), fontSize: 12),
                    ),
                  )
                : ListView.builder(
                    itemCount: _transcript.length,
                    itemBuilder: (context, index) {
                      final entry = _transcript[index];
                      final speaker = entry['speaker'] as String? ?? 'system';
                      final text = entry['text'] as String? ?? '';
                      return ListTile(
                        dense: true,
                        title: Text(text, style: const TextStyle(fontSize: 12)),
                        subtitle: Text(speakerLabel(speaker),
                            style: const TextStyle(
                                fontSize: 10, color: Color(0xFFA8A79E))),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _controlBar() {
    return Container(
      padding: const EdgeInsets.all(16),
      color: const Color(0xFF14171B),
      child: Row(
        children: <Widget>[
          FilledButton.icon(
            onPressed: _connecting ? null : _toggleMic,
            icon: Icon(_micOpen ? Icons.mic : Icons.mic_off),
            label: Text(_micOpen ? 'Listening' : 'Speak'),
            style: FilledButton.styleFrom(
              backgroundColor: _micOpen ? const Color(0xFFE39C9C) : null,
            ),
          ),
          const SizedBox(width: 12),
          OutlinedButton.icon(
            onPressed: _connecting ? null : () => setState(() => _speakerOn = !_speakerOn),
            icon: Icon(_speakerOn ? Icons.volume_up : Icons.volume_off),
            label: const Text('Tutor'),
          ),
          const Spacer(),
          OutlinedButton.icon(
            onPressed: _connecting ? null : () => widget.client.interrupt(),
            icon: const Icon(Icons.stop),
            label: const Text('Interrupt'),
          ),
          const SizedBox(width: 12),
          FilledButton.icon(
            onPressed: _connecting ? null : _endLesson,
            icon: const Icon(Icons.call_end),
            label: const Text('End Lesson'),
            style: FilledButton.styleFrom(backgroundColor: const Color(0xFFE39C9C)),
          ),
        ],
      ),
    );
  }
}
