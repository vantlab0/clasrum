import 'package:flutter/material.dart';

import '../../core/config.dart';
import '../../core/storage.dart';
import '../../core/theme.dart';
import '../../core/widgets/glass_widgets.dart';
import '../../core/widgets/pwony_icons.dart';
import '../../features/videos/videos_screen.dart';

/// Video player screen — plays a selected educational video in a focused viewer.
///
/// The embedded player is intentionally isolated behind the same dark Clasrum
/// frame as the rest of the app. Required YouTube branding/controls stay with
/// the player implementation; this screen only hosts it.
class VideoPlayerScreen extends StatefulWidget {
  const VideoPlayerScreen({
    super.key,
    required this.config,
    required this.storage,
    required this.video,
    required this.onVideoClosed,
  });

  final AppConfig config;
  final AppStorage storage;
  final VideoSearchResult video;
  final VoidCallback onVideoClosed;

  @override
  State<VideoPlayerScreen> createState() => _VideoPlayerScreenState();
}

class _VideoPlayerScreenState extends State<VideoPlayerScreen> {
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      setState(() => _isLoading = false);
    });
  }

  void _closeViewer() {
    widget.onVideoClosed();
    if (mounted) Navigator.of(context).maybePop();
  }

  @override
  Widget build(BuildContext context) {
    final Orientation orientation =
        MediaQuery.sizeOf(context).width > MediaQuery.sizeOf(context).height
            ? Orientation.landscape
            : Orientation.portrait;

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        _closeViewer();
      },
      child: Scaffold(
        backgroundColor: AppColors.background,
        body: _buildBody(orientation),
      ),
    );
  }

  Widget _buildBody(Orientation orientation) {
    if (_isLoading) return _buildLoadingState();
    if (_error != null) return _buildErrorState();
    return _buildPlayerArea(orientation);
  }

  Widget _buildLoadingState() {
    return Center(
      child: GlassContainer(
        radius: AppRadius.lg,
        padding: const EdgeInsets.all(24),
        blurSigma: 8,
        backgroundOpacity: 0.6,
        borderOpacity: 0.5,
        child: const Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            SizedBox(
              width: 40,
              height: 40,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            SizedBox(height: 16),
            Text(
              'Loading video…',
              style: TextStyle(color: AppColors.muted, fontSize: 14),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildErrorState() {
    return Center(
      child: GlassContainer(
        radius: AppRadius.lg,
        padding: const EdgeInsets.all(24),
        blurSigma: 8,
        backgroundOpacity: 0.6,
        borderOpacity: 0.5,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const PwonyIcon(
              PwonyIconKind.delete,
              size: 40,
              color: AppColors.red,
            ),
            const SizedBox(height: 16),
            const Text(
              'Unable to load video',
              style: TextStyle(
                color: AppColors.white,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              _error ?? 'Unknown error',
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.muted, fontSize: 14),
            ),
            const SizedBox(height: 16),
            GlassContainer(
              radius: AppRadius.md,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              blurSigma: 6,
              backgroundOpacity: 0.5,
              borderOpacity: 0.4,
              child: TextButton(
                onPressed: _closeViewer,
                child: const Text('Close'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPlayerArea(Orientation orientation) {
    final double aspectRatio =
        orientation == Orientation.landscape ? 16 / 9 : 16 / 9;

    return Stack(
      children: <Widget>[
        Center(
          child: AspectRatio(
            aspectRatio: aspectRatio,
            child: GlassContainer(
              color: AppColors.surface,
              child: Center(
                child: ConstrainedBox(
                  constraints: BoxConstraints(
                    minWidth: 200,
                    minHeight: 120,
                    maxWidth: MediaQuery.sizeOf(context).width * 0.92,
                    maxHeight: MediaQuery.sizeOf(context).height * 0.68,
                  ),
                  child: _buildVideoPlaceholder(),
                ),
              ),
            ),
          ),
        ),
        Positioned(
          left: AppSpacing.sm,
          top: AppSpacing.sm,
          child: GlassContainer(
            radius: AppRadius.xl,
            padding: const EdgeInsets.all(10),
            blurSigma: 6,
            backgroundOpacity: 0.5,
            borderOpacity: 0.4,
            child: InkWell(
              onTap: _closeViewer,
              borderRadius: BorderRadius.circular(AppRadius.xl),
              child: const PwonyIcon(
                PwonyIconKind.close,
                size: 24,
                color: AppColors.white,
              ),
            ),
          ),
        ),
        Positioned(
          bottom: AppSpacing.lg,
          left: AppSpacing.lg,
          right: AppSpacing.lg,
          child: GlassContainer(
            radius: AppRadius.lg,
            padding: const EdgeInsets.all(AppSpacing.md),
            blurSigma: 8,
            backgroundOpacity: 0.7,
            borderOpacity: 0.5,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  widget.video.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AppColors.white,
                    fontSize: 20,
                    fontWeight: FontWeight.w600,
                    height: 1.2,
                  ),
                ),
                const SizedBox(height: AppSpacing.xs),
                Row(
                  children: <Widget>[
                    const PwonyIcon(
                      PwonyIconKind.profile,
                      size: 12,
                      color: AppColors.muted,
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        'From: ${widget.video.channelTitle ?? "Unknown channel"}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: AppColors.muted,
                          fontSize: 12,
                        ),
                      ),
                    ),
                  ],
                ),
                if (widget.video.description != null &&
                    widget.video.description!.trim().isNotEmpty) ...<Widget>[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    _truncateText(widget.video.description!.trim(), 140),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AppColors.muted,
                      fontSize: 12,
                      height: 1.4,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }

  String _truncateText(String text, int maxLength) {
    if (text.length <= maxLength) return text;
    return '${text.substring(0, maxLength - 3)}...';
  }

  Widget _buildVideoPlaceholder() {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: <Widget>[
        const PwonyIcon(
          PwonyIconKind.play,
          size: 64,
          color: AppColors.muted,
        ),
        const SizedBox(height: 16),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Text(
            widget.video.title,
            textAlign: TextAlign.center,
            style: const TextStyle(color: AppColors.white, fontSize: 14),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          widget.video.channelTitle ?? 'Unknown channel',
          style: const TextStyle(color: AppColors.muted, fontSize: 12),
        ),
      ],
    );
  }
}
