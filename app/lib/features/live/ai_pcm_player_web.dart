// ignore: avoid_web_libraries_in_flutter
import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

@JS('AudioContext')
extension type _AudioContext._(JSObject _) implements JSObject {
  external factory _AudioContext();
  external JSPromise<JSAny?> resume();
  external double get currentTime;
  external _AudioBuffer createBuffer(
    int numberOfChannels,
    int length,
    double sampleRate,
  );
  external _BufferSource createBufferSource();
  external JSObject get destination;
  external JSPromise<JSAny?> close();
}

@JS('webkitAudioContext')
extension type _LegacyAudioContext._(JSObject _) implements JSObject {
  external factory _LegacyAudioContext();
}

@JS()
extension type _AudioBuffer._(JSObject _) implements JSObject {
  // Typed data cannot appear directly in an external interop signature under
  // dart2js; pass it across the boundary as JSAny via `toJS`.
  external void copyToChannel(JSAny? source, int channelNumber);
}

@JS()
extension type _BufferSource._(JSObject _) implements JSObject {
  external set buffer(_AudioBuffer? value);
  external void connect(JSObject destination);
  external void start(double when);
  external void stop();
  external set onended(JSFunction? handler);
}

/// Web PCM playback backed by the platform Web Audio API.
///
/// The previous implementation reached Web Audio through `dart:js_util`, which
/// is no longer part of the Dart SDK. The typed `dart:js_interop` bindings keep
/// the same queueing behaviour without adding a package dependency.
class AiPcmPlayer {
  _AudioContext? _ctx;
  bool _setup = false;
  int _sampleRate = 24000;
  int _feedThresholdFrames = 2400;
  void Function(int remainingFrames)? _feedCallback;
  Timer? _monitor;
  double _nextPlayTime = 0;
  final List<_BufferSource> _activeSources = <_BufferSource>[];

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
    try {
      _ctx = _AudioContext();
    } catch (_) {
      final legacy = _LegacyAudioContext() as JSObject;
      _ctx = legacy as _AudioContext;
    }
    final ctx = _ctx!;
    try {
      await ctx.resume().toDart;
    } catch (_) {}
    _nextPlayTime = ctx.currentTime;
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
      await ctx.resume().toDart;
    } catch (_) {}

    final floatData = Float32List(samples.length);
    for (var i = 0; i < samples.length; i++) {
      floatData[i] = samples[i] / 32768.0;
    }

    final buffer = ctx.createBuffer(1, samples.length, _sampleRate.toDouble());
    buffer.copyToChannel(floatData.toJS, 0);

    final source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    final now = ctx.currentTime;
    if (_nextPlayTime < now) _nextPlayTime = now;
    source.start(_nextPlayTime);
    _nextPlayTime += samples.length / _sampleRate;

    _activeSources.add(source);
    source.onended = ((JSAny? _) {
      _activeSources.remove(source);
      source.onended = null;
    }).toJS;

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
    final remainingSeconds = (_nextPlayTime - ctx.currentTime).clamp(0.0, 3600.0);
    final remainingFrames = (remainingSeconds * _sampleRate).round();
    if (remainingFrames <= _feedThresholdFrames) {
      _feedCallback?.call(remainingFrames);
    }
  }

  Future<void> stop() async {
    _stopSources();
    final ctx = _ctx;
    if (ctx != null) _nextPlayTime = ctx.currentTime;
  }

  Future<void> release() async {
    _monitor?.cancel();
    _monitor = null;
    _stopSources();
    final ctx = _ctx;
    _ctx = null;
    _setup = false;
    if (ctx != null) {
      try {
        await ctx.close().toDart;
      } catch (_) {}
    }
  }

  void _stopSources() {
    for (final source in List<_BufferSource>.from(_activeSources)) {
      try {
        source.stop();
      } catch (_) {}
      source.onended = null;
    }
    _activeSources.clear();
  }
}
