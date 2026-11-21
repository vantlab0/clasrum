import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/pwony_icons.dart';
import '../../features/video_player/video_player_screen.dart';

/// Discover Videos screen — students enter a topic and find educational videos.
class VideosScreen extends StatefulWidget {
  const VideosScreen({
    super.key,
    required this.config,
    required this.storage,
  });

  final AppConfig config;
  final AppStorage storage;

  @override
  State<VideosScreen> createState() => _VideosScreenState();
}

class VideoHistoryEntry {
  const VideoHistoryEntry({
    required this.videoId,
    required this.title,
    this.thumbnailUrl,
    this.channelTitle,
    required this.searchQuery,
    required this.watchedAt,
  });

  final String videoId;
  final String title;
  final String? thumbnailUrl;
  final String? channelTitle;
  final String searchQuery;
  final DateTime watchedAt;

  factory VideoHistoryEntry.fromJson(Map<String, dynamic> json) {
    return VideoHistoryEntry(
      videoId: json['video_id'] as String? ?? '',
      title: json['title'] as String? ?? 'Untitled video',
      thumbnailUrl: json['thumbnail_url'] as String?,
      channelTitle: json['channel_title'] as String?,
      searchQuery: json['search_query'] as String? ?? '',
      watchedAt: DateTime.tryParse(json['watched_at'] as String? ?? '') ??
          DateTime.now(),
    );
  }
}

class VideoWatchHistoryResponse {
  const VideoWatchHistoryResponse({
    required this.items,
    required this.total,
  });

  final List<VideoHistoryEntry> items;
  final int total;
}

class VideoSearchResult {
  const VideoSearchResult({
    required this.videoId,
    required this.title,
    this.description,
    this.channelTitle,
    this.thumbnailUrl,
    this.viewCount,
    this.duration,
    this.publishedAt,
  });

  final String videoId;
  final String title;
  final String? description;
  final String? channelTitle;
  final String? thumbnailUrl;
  final int? viewCount;
  final String? duration;
  final DateTime? publishedAt;

  factory VideoSearchResult.fromJson(Map<String, dynamic> json) {
    return VideoSearchResult(
      videoId: json['video_id'] as String? ?? '',
      title: json['title'] as String? ?? 'Untitled video',
      description: json['description'] as String?,
      channelTitle: json['channel_title'] as String?,
      thumbnailUrl: json['thumbnail_url'] as String?,
      viewCount: json['view_count'] as int?,
      duration: json['duration'] as String?,
      publishedAt: DateTime.tryParse(
        json['published_at'] as String? ?? '',
      ),
    );
  }
}

class _VideosScreenState extends State<VideosScreen> {
  final ScrollController _scrollController = ScrollController();
  final TextEditingController _searchController = TextEditingController();

  List<VideoSearchResult> _results = const <VideoSearchResult>[];
  List<VideoHistoryEntry> _history = const <VideoHistoryEntry>[];
  bool _isSearching = false;
  bool _historyLoading = true;
  String? _errorMessage;
  String? _refinedQuery;
  bool _hasRefinement = false;
  String? _lessonId;

  @override
  void initState() {
    super.initState();
    _lessonId = widget.storage.lastLessonId;
    _loadHistory();
  }

  @override
  void dispose() {
    _scrollController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  Map<String, String> get _authHeaders {
    final String? token = widget.storage.token;
    return <String, String>{
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Future<void> _loadHistory() async {
    final String? lessonId = _lessonId;
    if (lessonId == null || lessonId.isEmpty) {
      setState(() => _historyLoading = false);
      return;
    }

    setState(() => _historyLoading = true);
    try {
      final http.Response response = await http.get(
        widget.config.apiUri('/lessons/$lessonId/videos/history'),
        headers: _authHeaders,
      );
      if (!mounted) return;

      if (response.statusCode == 200) {
        final Map<String, dynamic> body =
            jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
        final List<dynamic> items = body['items'] as List<dynamic>? ?? [];
        setState(() {
          _history = items
              .whereType<Map<String, dynamic>>()
              .map(VideoHistoryEntry.fromJson)
              .toList(growable: false);
          _historyLoading = false;
        });
        return;
      }

      setState(() => _historyLoading = false);
    } catch (_) {
      if (mounted) setState(() => _historyLoading = false);
    }
  }

  Future<String> _resolveLessonId(String query) async {
    final String? existing = _lessonId;
    if (existing != null && existing.isNotEmpty) return existing;

    final String? lastLesson = widget.storage.lastLessonId;
    if (lastLesson != null && lastLesson.isNotEmpty) {
      _lessonId = lastLesson;
      return lastLesson;
    }

    final String learningLevel =
        widget.storage.defaultLearningLevel ?? 'high_school';
    final http.Response response = await http.post(
      widget.config.apiUri('/lessons'),
      headers: _authHeaders,
      body: jsonEncode(<String, dynamic>{
        'topic': query,
        'learning_level': learningLevel,
        'duration_minutes': widget.storage.defaultDurationMinutes ?? 30,
      }),
    );
    if (response.statusCode != 200) {
      throw Exception('Could not create a lesson for video search.');
    }
    final Map<String, dynamic> body =
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    final String id = body['id'] as String;
    _lessonId = id;
    await widget.storage.setLastLessonId(id);
    await widget.storage.markLessonOpened(id);
    return id;
  }

  Future<void> _performSearch() async {
    final String query = _searchController.text.trim();
    if (query.length < 2) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Enter a topic with at least 2 characters.'),
          ),
        );
      }
      return;
    }

    setState(() {
      _isSearching = true;
      _errorMessage = null;
    });

    try {
      final String lessonId = await _resolveLessonId(query);
      final http.Response response = await http.post(
        widget.config.apiUri('/lessons/$lessonId/videos/search'),
        headers: _authHeaders,
        body: jsonEncode(<String, dynamic>{
          'query': query,
          'subject': null,
          'class_level': null,
        }),
      );
      if (!mounted) return;

      final Map<String, dynamic> body =
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;

      if (response.statusCode == 200) {
        final List<dynamic> videos = body['videos'] as List<dynamic>? ?? [];
        setState(() {
          _results = videos
              .whereType<Map<String, dynamic>>()
              .map(VideoSearchResult.fromJson)
              .toList(growable: false);
          _refinedQuery = body['refined_query'] as String?;
          _hasRefinement = body['has_refinement'] as bool? ?? false;
          _isSearching = false;
        });
        return;
      }

      final Map<String, dynamic>? error =
          body['error'] as Map<String, dynamic>?;
      final String message = error?['message'] as String? ??
          'Video search failed (HTTP ${response.statusCode}).';
      setState(() {
        _isSearching = false;
        _errorMessage = message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _isSearching = false;
        _errorMessage = 'Could not search for videos. Please try again.';
      });
    }
  }

  void _openVideo(VideoSearchResult video) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => VideoPlayerScreen(
          config: widget.config,
          storage: widget.storage,
          video: video,
          onVideoClosed: () {
            if (mounted) setState(() {});
          },
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        top: true,
        child: Column(
          children: <Widget>[
            _buildHeader(context),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: GlassContainer(
                radius: AppRadius.lg,
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 10,
                ),
                blurSigma: 8,
                backgroundOpacity: 0.6,
                borderOpacity: 0.5,
                child: Row(
                  children: <Widget>[
                    const PwonyIcon(
                      PwonyIconKind.search,
                      size: 18,
                      color: AppColors.muted,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _searchController,
                        onSubmitted: (_) => _performSearch(),
                        style: const TextStyle(color: AppColors.white),
                        decoration: const InputDecoration(
                          border: InputBorder.none,
                          enabledBorder: InputBorder.none,
                          focusedBorder: InputBorder.none,
                          filled: false,
                          hintText: 'What do you want to learn today?',
                          hintStyle: TextStyle(color: AppColors.muted),
                          isDense: true,
                        ),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Search',
                      onPressed: _isSearching ? null : _performSearch,
                      icon: const PwonyIcon(
                        PwonyIconKind.chevronRight,
                        size: 18,
                        color: AppColors.white,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            if (_hasRefinement && (_refinedQuery ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Text(
                  'Refined to: $_refinedQuery',
                  style: const TextStyle(
                    color: AppColors.muted,
                    fontSize: 11,
                  ),
                ),
              ),
            Expanded(child: _buildBody()),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 4, 16, 8),
      child: Row(
        children: <Widget>[
          IconButton(
            tooltip: 'Back',
            onPressed: () => Navigator.of(context).maybePop(),
            icon: const PwonyIcon(PwonyIconKind.back, size: 22),
          ),
          const SizedBox(width: 8),
          const Text(
            'Discover Videos',
            style: TextStyle(
              color: AppColors.white,
              fontSize: 20,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_isSearching) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            CircularProgressIndicator(strokeWidth: 2),
            SizedBox(height: 16),
            Text(
              'Searching for videos…',
              style: TextStyle(color: AppColors.muted, fontSize: 14),
            ),
          ],
        ),
      );
    }

    if (_errorMessage != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            _errorMessage!,
            textAlign: TextAlign.center,
            style: const TextStyle(color: AppColors.muted, fontSize: 14),
          ),
        ),
      );
    }

    if (_results.isNotEmpty) return _buildResultsList();

    if (_historyLoading) {
      return const Center(
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }

    if (_history.isNotEmpty) return _buildHistoryList();

    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            const PwonyIcon(
              PwonyIconKind.play,
              size: 46,
              color: AppColors.muted,
            ),
            const SizedBox(height: 16),
            const Text(
              'Search for a topic to find educational videos.',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: AppColors.muted,
                fontSize: 14,
                height: 1.5,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildResultsList() {
    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      itemCount: _results.length,
      itemBuilder: (context, index) {
        final VideoSearchResult video = _results[index];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _VideoCard(
            video: video,
            onTap: () => _openVideo(video),
          ),
        );
      },
    );
  }

  Widget _buildHistoryList() {
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      itemCount: _history.length,
      itemBuilder: (context, index) {
        final VideoHistoryEntry entry = _history[index];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: GlassContainer(
            radius: AppRadius.md,
            padding: const EdgeInsets.all(10),
            blurSigma: 6,
            backgroundOpacity: 0.6,
            borderOpacity: 0.4,
            child: Row(
              children: <Widget>[
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: SizedBox(
                    width: 74,
                    height: 52,
                    child: _thumbnail(entry.thumbnailUrl),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        entry.title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: AppColors.white,
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      if ((entry.channelTitle ?? '').trim().isNotEmpty)
                        Text(
                          entry.channelTitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: AppColors.muted,
                            fontSize: 11,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _thumbnail(String? url) {
    if (url == null || url.isEmpty) {
      return const ColoredBox(
        color: AppColors.surfaceHigh,
        child: Center(
          child: PwonyIcon(
            PwonyIconKind.play,
            size: 20,
            color: AppColors.muted,
          ),
        ),
      );
    }
    return Image.network(
      url,
      fit: BoxFit.cover,
      errorBuilder: (context, error, stackTrace) {
        return const ColoredBox(
          color: AppColors.surfaceHigh,
          child: Center(
            child: PwonyIcon(
              PwonyIconKind.play,
              size: 20,
              color: AppColors.muted,
            ),
          ),
        );
      },
    );
  }
}

class _VideoCard extends StatelessWidget {
  const _VideoCard({required this.video, required this.onTap});

  final VideoSearchResult video;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GlassContainer(
      radius: AppRadius.md,
      padding: const EdgeInsets.all(8),
      blurSigma: 6,
      backgroundOpacity: 0.6,
      borderOpacity: 0.4,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Row(
          children: <Widget>[
            ClipRRect(
              borderRadius: BorderRadius.circular(10),
              child: SizedBox(
                width: 96,
                height: 72,
                child: _thumbnail(video.thumbnailUrl),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: <Widget>[
                  Text(
                    video.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AppColors.white,
                      fontSize: 13,
                      fontWeight: FontWeight.w500,
                      height: 1.3,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    video.channelTitle ?? 'Unknown channel',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AppColors.muted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _thumbnail(String? url) {
    if (url == null || url.isEmpty) {
      return const ColoredBox(
        color: AppColors.surfaceHigh,
        child: Center(
          child: PwonyIcon(
            PwonyIconKind.play,
            size: 22,
            color: AppColors.muted,
          ),
        ),
      );
    }
    return Image.network(
      url,
      fit: BoxFit.cover,
      errorBuilder: (context, error, stackTrace) {
        return const ColoredBox(
          color: AppColors.surfaceHigh,
          child: Center(
            child: PwonyIcon(
              PwonyIconKind.play,
              size: 22,
              color: AppColors.muted,
            ),
          ),
        );
      },
    );
  }
}
