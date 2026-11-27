import 'package:flutter_test/flutter_test.dart';
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

    test('recognizes user.speech_started from semantic VAD', () {
      expect(RealtimeClient.parseType('user.speech_started'),
          RealtimeMessageType.userSpeechStarted);
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
}
