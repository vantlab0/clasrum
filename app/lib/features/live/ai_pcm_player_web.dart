// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'dart:js_util' as js_util;
import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

class AiPcmPlayer {
  dynamic _ctx;
  bool _setup = false;
  int _sampleRate = 24000;
  int _feedThresholdFrames = 2400;
  void Function(int remainingFrames)? _feedCallback;
  Timer? _monitor;
  double _nextPlayTime = 0;
  final List<dynamic> _activeSources = <dynamic>[];

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
    final constructor = js_util.getProperty(html.window, 'AudioContext') ??
        js_util.getProperty(html.window, 'webkitAudioContext');
    _ctx = js_util.callConstructor(constructor, [js_util.jsify({'sampleRate': _sampleRate})]);
    try {
      await js_util.promiseToFuture(js_util.callMethod(_ctx, 'resume', []));
    } catch (_) {}
    _nextPlayTime = (_ctx.currentTime as num?)?.toDouble() ?? 0.0;
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
      await js_util.promiseToFuture(js_util.callMethod(ctx, 'resume', []));
    } catch (_) {}

    final floatData = Float32List(samples.length);
    for (var i = 0; i < samples.length; i++) {
      floatData[i] = samples[i] / 32768.0;
    }

    final buffer = js_util.callMethod(ctx, 'createBuffer', [1, samples.length, _sampleRate]);
    js_util.callMethod(buffer, 'copyToChannel', [floatData, 0]);

    final source = js_util.callMethod(ctx, 'createBufferSource', []);
    js_util.setProperty(source, 'buffer', buffer);
    js_util.callMethod(source, 'connect', [js_util.getProperty(ctx, 'destination')]);

    final now = (js_util.getProperty(ctx, 'currentTime') as num?)?.toDouble() ?? 0.0;
    if (_nextPlayTime < now) _nextPlayTime = now;
    js_util.callMethod(source, 'start', [_nextPlayTime]);
    _nextPlayTime += samples.length / _sampleRate;

    _activeSources.add(source);
    js_util.setProperty(source, 'onended', js_util.allowInterop((_) {
      _activeSources.remove(source);
    }));

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
    final now = (js_util.getProperty(ctx, 'currentTime') as num?)?.toDouble() ?? 0.0;
    final remainingSeconds = (_nextPlayTime - now).clamp(0.0, 3600.0);
    final remainingFrames = (remainingSeconds * _sampleRate).round();
    if (remainingFrames <= _feedThresholdFrames) {
      _feedCallback?.call(remainingFrames);
    }
  }

  Future<void> stop() async {
    for (final s in List<dynamic>.from(_activeSources)) {
      try {
        js_util.callMethod(s, 'stop', []);
      } catch (_) {}
    }
    _activeSources.clear();
    final ctx = _ctx;
    if (ctx != null) {
      _nextPlayTime =
          (js_util.getProperty(ctx, 'currentTime') as num?)?.toDouble() ?? 0.0;
    }
  }

  Future<void> release() async {
    _monitor?.cancel();
    _monitor = null;
    for (final s in List<dynamic>.from(_activeSources)) {
      try {
        js_util.callMethod(s, 'stop', []);
      } catch (_) {}
    }
    _activeSources.clear();
    final ctx = _ctx;
    _ctx = null;
    _setup = false;
    if (ctx != null) {
      try {
        await js_util.promiseToFuture(js_util.callMethod(ctx, 'close', []));
      } catch (_) {}
    }
  }
}
