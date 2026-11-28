import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:pwony/blackboard/model/board_geometry.dart';
import 'package:pwony/services/realtime_client.dart';

void main() {
  group('RealtimeClient.parseType', () {
    test('recognizes connected acknowledgement', () {
      expect(
          RealtimeClient.parseType('connected'), RealtimeMessageType.connected);
    });

    test('recognizes session.ready', () {
      expect(RealtimeClient.parseType('session.ready'),
          RealtimeMessageType.sessionReady);
    });

    test('recognizes user.speech_started as legacy telemetry', () {
      expect(RealtimeClient.parseType('user.speech_started'),
          RealtimeMessageType.userSpeechStarted);
    });

    test('recognizes manual-mode microphone gate messages', () {
      expect(RealtimeClient.parseType('mic.unlock'),
          RealtimeMessageType.micUnlock);
      expect(RealtimeClient.parseType('mic.lock'),
          RealtimeMessageType.micLock);
    });

    test('recognizes session.error as error', () {
      expect(
          RealtimeClient.parseType('session.error'), RealtimeMessageType.error);
    });

    test('treats unknown message as unknown (not fatal error)', () {
      expect(RealtimeClient.parseType('some.future.message'),
          RealtimeMessageType.unknown);
    });

    test('treats null as unknown', () {
      expect(RealtimeClient.parseType(null), RealtimeMessageType.unknown);
    });
  });

  group('RealtimeClient manual-mode payloads', () {
    test('audio.start carries the explicit answer request id', () {
      final payload = RealtimeClient.audioStartPayload(requestId: 'req_42');
      expect(payload['type'], 'audio.start');
      expect(payload['sample_rate'], 16000);
      expect(payload['format'], 'pcm16');
      expect(payload['reason'], 'answer');
      expect(payload['request_id'], 'req_42');
    });

    test('audio.stop distinguishes submit from cancel and keeps request id', () {
      final submitted =
          RealtimeClient.audioStopPayload(submitted: true, requestId: 'req_42');
      expect(submitted['type'], 'audio.stop');
      expect(submitted['submitted'], isTrue);
      expect(submitted['request_id'], 'req_42');

      final cancelled = RealtimeClient.audioStopPayload(
        submitted: false,
        requestId: 'req_42',
      );
      expect(cancelled['submitted'], isFalse);
      expect(cancelled['request_id'], 'req_42');

      final noId = RealtimeClient.audioStopPayload(submitted: false);
      expect(noId.containsKey('request_id'), isFalse);
    });

    test('board.clear carries a client sequence for idempotency', () {
      expect(
        RealtimeClient.boardClearPayload(sequence: 12),
        <String, dynamic>{'type': 'board.clear', 'sequence': 12},
      );
      expect(RealtimeClient.boardClearPayload(sequence: -1)['sequence'], 0);
    });

    test('viewport_full report is clamped and carries measured rect', () {
      final payload = RealtimeClient.viewportFullPayload(
        sequence: -3,
        coverage: 1.4,
        visibleRect: const BoardRect(0.1, 0.2, 0.8, 0.7),
        scale: 9.0,
        offset: const Offset(0.25, 0.5),
        narrow: true,
        contentExtent: 2.4,
        reason: 'scroll_bottom',
      );
      expect(payload['type'], 'board.viewport_full');
      expect(payload['sequence'], 0);
      expect(payload['coverage'], 1.0);
      expect(payload['scale'], 2.0);
      expect(payload['visible_rect'],
          <String, dynamic>{'x': 0.1, 'y': 0.2, 'w': 0.8, 'h': 0.7});
      expect(payload['offset'], <String, dynamic>{'x': 0.25, 'y': 0.5});
      expect(payload['layout_mode'], 'mobile');
      expect(payload['content_extent'], 2.4);
      expect(payload['reason'], 'scroll_bottom');
    });
  });

  group('RealtimeMessage microphone gate', () {
    test('mic.unlock exposes request id, reason and answer requirement', () {
      final message = RealtimeMessage(
        type: RealtimeMessageType.micUnlock,
        payload: <String, dynamic>{
          'type': 'mic.unlock',
          'lesson_id': 'lesson-1',
          'request_id': 'req_7',
          'reason': 'wait_for_student',
          'requires_answer': true,
          'turn_detection': 'manual',
        },
      );
      expect(message.type, RealtimeMessageType.micUnlock);
      expect(message.micRequestId, 'req_7');
      expect(message.micReason, 'wait_for_student');
      expect(message.micRequiresAnswer, isTrue);
    });

    test('mic.lock carries reason without an answer requirement', () {
      final message = RealtimeMessage(
        type: RealtimeMessageType.micLock,
        payload: <String, dynamic>{
          'type': 'mic.lock',
          'reason': 'answer_submitted',
          'requires_answer': false,
        },
      );
      expect(message.type, RealtimeMessageType.micLock);
      expect(message.micReason, 'answer_submitted');
      expect(message.micRequiresAnswer, isFalse);
    });
  });
}
