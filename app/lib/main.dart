import 'package:flutter/material.dart';

import 'app_shell.dart';
import 'core/config.dart';
import 'core/storage.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final config = AppConfig.fromEnvironment();
  final storage = await AppStorage.open();
  runApp(AiClassroomApp(config: config, storage: storage));
}

class AiClassroomApp extends StatelessWidget {
  const AiClassroomApp({super.key, required this.config, required this.storage});

  final AppConfig config;
  final AppStorage storage;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'AI Personal Classroom',
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF101215),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFF2F0E8),
          secondary: Color(0xFFE8D58C),
          surface: Color(0xFF171A1F),
          background: Color(0xFF101215),
        ),
      ),
      home: AppShell(config: config, storage: storage),
    );
  }
}
