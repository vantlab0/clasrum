import 'package:flutter/material.dart';

import '../core/config.dart';
import '../core/storage.dart';
import '../features/home/home_screen.dart';
import '../features/live/live_screen.dart';
import '../features/notes/notes_screen.dart';
import '../features/profile/profile_screen.dart';
import '../services/realtime_client.dart';

/// Shell with exactly three NavigationDestination tabs (CONTRACTS §10):
/// Home, Notes, Profile.
class AppShell extends StatefulWidget {
  const AppShell({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  RealtimeClient? _realtimeClient;

  @override
  void dispose() {
    _realtimeClient?.dispose();
    super.dispose();
  }

  RealtimeClient _client() => _realtimeClient ??= RealtimeClient(
        config: widget.config,
        storage: widget.storage,
      );

  @override
  Widget build(BuildContext context) {
    return RealtimeClientScope(
      notifier: _client(),
      child: Scaffold(
        body: IndexedStack(
          index: _index,
          children: <Widget>[
            HomeScreen(
              config: widget.config,
              storage: widget.storage,
              onStartLesson: (lessonId) {
                _client().connect(lessonId: lessonId);
                setState(() => _index = 2); // live tab
              },
            ),
            NotesScreen(config: widget.config, storage: widget.storage),
            ProfileScreen(config: widget.config, storage: widget.storage),
            LiveScreen(client: _client()),
          ],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: (i) => setState(() => _index = i),
          destinations: const <NavigationDestination>[
            NavigationDestination(
              icon: Icon(Icons.home_outlined),
              selectedIcon: Icon(Icons.home),
              label: 'Home',
            ),
            NavigationDestination(
              icon: Icon(Icons.note_outlined),
              selectedIcon: Icon(Icons.note),
              label: 'Notes',
            ),
            NavigationDestination(
              icon: Icon(Icons.person_outline),
              selectedIcon: Icon(Icons.person),
              label: 'Profile',
            ),
          ],
        ),
      ),
    );
  }
}
