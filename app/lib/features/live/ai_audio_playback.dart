import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';

/// Playback lifecycle for incoming AI (tutor) audio.
enum AiPlaybackState {
  idle,
  buffering,
  playing,
  paused,
  interrupted,
  completed,
  error,
}

class _AudioChunk {
  const _AudioChunk({
    required this.samples,
    required this.responseId,
    required this.sequence,
  });

  final Int16List samples;
  final int? responseId;
  final int sequence;
}

/// Streams base64 PCM16 mono frames from the realtime client into
/// `flutter_pcm_sound`, with pause/resume, ordering, and interruption handling.
///
/// Notes:
/// - `flutter_pcm_sound` has no native pause primitive; this controller
///   preserves playback position by keeping app-side buffered frames and only
///   feeding while in the [AiPlaybackState.playing] state.
/// - To keep pauses responsive, feed-ahead is kept small via a low threshold.
class AiAudioPlaybackController extends ChangeNotifier {
  AiAudioPlaybackController({
    this.defaultSampleRate = 24000,
    this.feedThresholdMs = 120,
  });

  final int defaultSampleRate;
  final int feedThresholdMs;

  AiPlaybackState _state = AiPlaybackState.idle;
  AiPlaybackState get state => _state;

  int _sampleRate = 24000;
  int get sampleRate => _sampleRate;

  bool _setup = false;
  bool _disposed = false;
  bool _feedInFlight = false;
  bool _playbackEnabled = false; // user intent (not paused)
  bool _responseDone = false;
  int? _activeResponseId;
  int _nextSequence = 0;

  final Queue<_AudioChunk> _queue = Queue<_AudioChunk>();
  String? _lastError;
  String? get lastError => _lastError;

  bool get isPlaying => _state == AiPlaybackState.playing;
  bool get isPaused => _state == AiPlaybackState.paused;
  bool get hasBufferedAudio => _queue.isNotEmpty;

  /// Ensures the PCM output is configured for [sampleRate]. Reconfigures only
  /// when needed and safe (no buffered/active audio).
  Future<void> ensureInitialized({int? sampleRate}) async {
    if (_disposed) return;
    final desired = _normalizeSampleRate(sampleRate);
    if (_setup && _sampleRate == desired) return;

    // Re-setup only when idle/empty to avoid corrupting in-flight playback.
    if (_setup && (_queue.isNotEmpty || _state == AiPlaybackState.playing)) {
      return;
    }

    try {
      if (_setup) {
        await FlutterPcmSound.release();
        _setup = false;
      }
      _sampleRate = desired;
      await FlutterPcmSound.setup(sampleRate: _sampleRate, channelCount: 1);
      final thresholdFrames =
          (_sampleRate * feedThresholdMs / 1000).round().clamp(256, 48000);
      await FlutterPcmSound.setFeedThreshold(thresholdFrames);
      FlutterPcmSound.setFeedCallback(_onFeedCallback);
      FlutterPcmSound.start();
      _setup = true;
      _lastError = null;
    } catch (e) {
      _setup = false;
      _setError('Audio playback could not be initialized.');
      debugPrint('[AiAudioPlayback] setup failed: $e');
    }
  }

  /// Enqueues an incoming `audio.delta` frame.
  Future<void> enqueueDelta({
    required String base64Pcm16,
    int? sampleRate,
    int? sequence,
    int? responseId,
  }) async {
    if (_disposed) return;
    try {
      await ensureInitialized(sampleRate: sampleRate);
      if (!_setup) return;

      // New response after completion/interruption: reset response tracking.
      if (_state == AiPlaybackState.completed ||
          _state == AiPlaybackState.interrupted ||
          _state == AiPlaybackState.error) {
        _responseDone = false;
      }

      final bytes = base64Decode(base64Pcm16);
      if (bytes.isEmpty) return;
      final samples = _bytesToInt16Le(bytes);
      if (samples.isEmpty) return;

      final seq = sequence ?? _nextSequence++;
      if (sequence != null && sequence >= _nextSequence) {
        _nextSequence = sequence + 1;
      }

      _activeResponseId ??= responseId;
      _queue.add(_AudioChunk(
        samples: samples,
        responseId: responseId ?? _activeResponseId,
        sequence: seq,
      ));

      // Drop UI-blocking: just schedule feeding.
      if (_playbackEnabled) {
        if (_state != AiPlaybackState.playing) {
          _setState(AiPlaybackState.buffering);
        }
        unawaited(_pumpFeed());
      } else {
        // Paused by user intent: buffer safely for correct-order resume.
        if (_state != AiPlaybackState.paused) {
          _setState(AiPlaybackState.paused);
        }
      }
    } catch (e) {
      debugPrint('[AiAudioPlayback] enqueueDelta failed: $e');
      // Malformed frame must not crash the classroom.
    }
  }

  /// Marks the current response audio as fully received.
  void markDone({int? responseId}) {
    if (_disposed) return;
    if (responseId != null &&
        _activeResponseId != null &&
        responseId != _activeResponseId) {
      return; // stale done for an older response
    }
    _responseDone = true;
    if (_playbackEnabled) {
      unawaited(_pumpFeed());
    }
  }

  /// Pauses playback, preserving app-side buffered audio for resume.
  Future<void> pause() async {
    if (_disposed) return;
    _playbackEnabled = false;
    if (_queue.isNotEmpty ||
        _state == AiPlaybackState.playing ||
        _state == AiPlaybackState.buffering) {
      _setState(AiPlaybackState.paused);
    } else {
      _setState(AiPlaybackState.paused);
    }
  }

  /// Resumes playback, enabling feed for buffered and future incoming audio.
  Future<void> resume() async {
    if (_disposed) return;
    _playbackEnabled = true;
    _lastError = null;
    if (_queue.isNotEmpty) {
      _setState(AiPlaybackState.buffering);
      await ensureInitialized(sampleRate: _sampleRate);
      await _pumpFeed();
    } else if (_responseDone) {
      _completeIfDrained();
    } else if (_state == AiPlaybackState.paused ||
        _state == AiPlaybackState.interrupted ||
        _state == AiPlaybackState.completed ||
        _state == AiPlaybackState.error) {
      _setState(AiPlaybackState.idle);
    }
  }

  /// Stops playback for the current (interrupted) response and clears only its
  /// buffered audio. Newer response audio (different response id) is preserved.
  Future<void> interrupt({int? interruptedResponseId, bool keepNewer = true}) async {
    if (_disposed) return;
    _playbackEnabled = false;
    _responseDone = false;

    if (keepNewer && interruptedResponseId != null) {
      _queue.removeWhere((c) => c.responseId == interruptedResponseId);
    } else if (keepNewer && _activeResponseId != null) {
      _queue.removeWhere((c) => c.responseId == _activeResponseId);
    } else {
      _queue.clear();
    }

    _activeResponseId = _queue.isNotEmpty ? _queue.last.responseId : null;
    _setState(AiPlaybackState.interrupted);
    // Stop feeding; any already-fed native tail is minimal due to low threshold.
  }

  /// Hard reset (e.g. disconnect/end). Drops all buffered audio.
  Future<void> reset({AiPlaybackState endState = AiPlaybackState.idle}) async {
    if (_disposed) return;
    _playbackEnabled = false;
    _responseDone = false;
    _queue.clear();
    _activeResponseId = null;
    _setState(endState);
  }

  void _onFeedCallback(int remainingFrames) {
    if (_disposed) return;
    // remainingFrames == 0 means native buffer drained.
    if (remainingFrames == 0) {
      if (_queue.isEmpty && _responseDone && _playbackEnabled) {
        _completeIfDrained();
      } else if (_queue.isEmpty && !_playbackEnabled && _state != AiPlaybackState.paused) {
        // drained while paused/stopped feeding
      }
      return;
    }
    // Low-buffer event: top up if playing.
    if (_playbackEnabled) {
      unawaited(_pumpFeed());
    }
  }

  Future<void> _pumpFeed() async {
    if (_disposed || !_setup || _feedInFlight || !_playbackEnabled) return;
    if (_queue.isEmpty) {
      if (_responseDone) {
        // Wait for native drain via callback (remainingFrames==0).
      }
      return;
    }

    _feedInFlight = true;
    try {
      // Feed in small chunks to keep latency low and pause responsive.
      final chunk = _queue.removeFirst();
      _activeResponseId ??= chunk.responseId;
      await FlutterPcmSound.feed(PcmArrayInt16.fromList(chunk.samples));
      if (_disposed) return;
      if (_state != AiPlaybackState.playing && _playbackEnabled) {
        _setState(AiPlaybackState.playing);
      }
      // Continue feeding while there is buffered audio and we're playing.
      if (_playbackEnabled && _queue.isNotEmpty) {
        unawaited(_pumpFeed());
      } else if (_playbackEnabled && _queue.isEmpty && _responseDone) {
        // completion will be signaled when native buffer drains (callback == 0)
      }
    } catch (e) {
      debugPrint('[AiAudioPlayback] feed failed: $e');
      _setError('Audio playback failed.');
    } finally {
      _feedInFlight = false;
    }
  }

  void _completeIfDrained() {
    if (_disposed) return;
    if (_queue.isEmpty && _responseDone) {
      _playbackEnabled = false;
      _activeResponseId = null;
      _responseDone = false;
      _setState(AiPlaybackState.completed);
    }
  }

  void _setError(String message) {
    _lastError = message;
    _playbackEnabled = false;
    _setState(AiPlaybackState.error);
  }

  void _setState(AiPlaybackState next) {
    if (_disposed || _state == next) return;
    _state = next;
    notifyListeners();
  }

  static Int16List _bytesToInt16Le(Uint8List bytes) {
    final length = bytes.lengthInBytes - (bytes.lengthInBytes % 2);
    if (length <= 0) return Int16List(0);
    final bd = bytes.buffer.asByteData(bytes.offsetInBytes, length);
    final out = Int16List(length ~/ 2);
    for (var i = 0, j = 0; i < length; i += 2, j++) {
      out[j] = bd.getInt16(i, Endian.little);
    }
    return out;
  }

  static int _normalizeSampleRate(int? rate) {
    const allowed = <int>{8000, 16000, 22050, 24000, 44100, 48000};
    final r = rate ?? 24000;
    if (allowed.contains(r)) return r;
    if (r >= 40000) return 48000;
    if (r >= 28000) return 44100;
    if (r >= 20000) return 24000;
    if (r >= 18000) return 22050;
    if (r >= 12000) return 16000;
    return 8000;
  }

  @override
  void dispose() {
    _disposed = true;
    _playbackEnabled = false;
    _queue.clear();
    if (_setup) {
      FlutterPcmSound.setFeedCallback(null);
      unawaited(FlutterPcmSound.release());
      _setup = false;
    }
    super.dispose();
  }
}
