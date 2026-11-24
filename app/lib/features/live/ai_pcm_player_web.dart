// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

class AiPcmPlayer {
  html.AudioContext? _ctx;
  bool _setup = false;
  int _sampleRate = 24000;
  int _feedThresholdFrames = 2400;
  void Function(int remainingFrames)? _feedCallback;
  Timer? _monitor;
  double _nextPlayTime = 0;
  final List<html.AudioBufferSourceNode> _activeSources = <html.AudioBufferSourceNode>[];

  int get sampleRate => _sampleRate;
  bool get isSetup => _setup;

  Future<void> setup({
    required int sampleRate,
    required int feedThresholdFrames,
  }) async {
    if (_setup) {
      await release();
    }
    _sampleRate = sampleRate;
    _feedThresholdFrames = feedThresholdFrames;
    _ctx = html.AudioContext({'sampleRate': _sampleRate});
    try {
      await _ctx!.resume();
    } catch (_) {}
    _nextPlayTime = _ctx!.currentTime ?? 0;
    _setup = true;
    _startMonitor();
  }

  void setFeedCallback(void Function(int remainingFrames)? callback) {
    _feedCallback = callback;
  }

  Future<void> feed(Int16List samples) async {
    final ctx = _ctx;
    if (!_setup || ctx == null || samples.isEmpty) return;
    try {
      await ctx.resume();
    } catch (_) {}

    final floatData = Float32List(samples.length);
    for (var i = 0; i < samples.length; i++) {
      floatData[i] = samples[i] / 32768.0;
    }

    final buffer = ctx.createBuffer(1, samples.length, _sampleRate);
    // copyToChannel expects JS typed array; dart:html wraps it.
    buffer.copyToChannel(floatData, 0);

    final source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connectNode(ctx.destination!);

    final now = ctx.currentTime ?? 0;
    if (_nextPlayTime < now) _nextPlayTime = now;
    source.start(_nextPlayTime);
    _nextPlayTime += samples.length / _sampleRate;

    _activeSources.add(source);
    source.onEnded.listen((_) {
      _activeSources.remove(source);
    });

    _emitFeedState();
  }

  void _startMonitor() {
    _monitor?.cancel();
    _monitor = Timer.periodic(const Duration(milliseconds: 60), (_) {
      _emitFeedState();
    });
  }

  void _emitFeedState() {
    final ctx = _ctx;
    if (!_setup || ctx == null) return;
    final now = ctx.currentTime ?? 0;
    final remainingSeconds = (_nextPlayTime - now).clamp(0.0, 3600.0);
    final remainingFrames = (remainingSeconds * _sampleRate).round();
    if (remainingFrames <= _feedThresholdFrames) {
      _feedCallback?.call(remainingFrames);
    }
  }

  Future<void> stop() async {
    for (final s in List<html.AudioBufferSourceNode>.from(_activeSources)) {
      try {
        s.stop();
      } catch (_) {}
    }
    _activeSources.clear();
    final ctx = _ctx;
    if (ctx != null) {
      _nextPlayTime = ctx.currentTime ?? 0;
    }
  }

  Future<void> release() async {
    _monitor?.cancel();
    _monitor = null;
    for (final s in List<html.AudioBufferSourceNode>.from(_activeSources)) {
      try {
        s.stop();
      } catch (_) {}
    }
    _activeSources.clear();
    final ctx = _ctx;
    _ctx = null;
    _setup = false;
    if (ctx != null) {
      try {
        await ctx.close();
      } catch (_) {}
    }
  }
}
