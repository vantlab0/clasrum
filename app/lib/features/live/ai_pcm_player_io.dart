import 'dart:typed_data';

import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';

class AiPcmPlayer {
  bool _setup = false;
  int _sampleRate = 24000;
  void Function(int remainingFrames)? _feedCallback;

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
    await FlutterPcmSound.setup(sampleRate: _sampleRate, channelCount: 1);
    await FlutterPcmSound.setFeedThreshold(feedThresholdFrames);
    FlutterPcmSound.setFeedCallback((remaining) {
      _feedCallback?.call(remaining);
    });
    FlutterPcmSound.start();
    _setup = true;
  }

  void setFeedCallback(void Function(int remainingFrames)? callback) {
    _feedCallback = callback;
  }

  Future<void> feed(Int16List samples) async {
    await FlutterPcmSound.feed(PcmArrayInt16.fromList(samples));
  }

  Future<void> stop() async {
    // flutter_pcm_sound has no explicit flush; feeding is controlled by the caller.
  }

  Future<void> release() async {
    if (!_setup) return;
    FlutterPcmSound.setFeedCallback(null);
    await FlutterPcmSound.release();
    _setup = false;
  }
}
