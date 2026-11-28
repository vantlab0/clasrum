import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show Offset, Size;

import 'package:flutter/animation.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart' show TickerProvider;

import '../model/board_document.dart';
import '../model/board_geometry.dart';
import '../model/board_transform.dart';

/// Intelligent blackboard viewport controller.
///
/// Provides smooth auto-pan / zoom so that important board content (new
/// elements, highlighted elements, groups of elements) enters the comfortable
/// viewing area without requiring the student to drag the board manually.
///
/// The controller owns a virtual vertical scroll extent ([contentExtent]).
/// Normalized board coordinates may extend below y=1; the extent is the
/// authoritative clamp/focus boundary. Automatic following is enabled by
/// default, suspended by manual interaction, and resumed after [manualCooldown]
/// without a jarring jump.
class BoardViewportController extends ChangeNotifier {
  BoardViewportController({
    this.mobileBreakpoint = 600.0,
    this.manualCooldown = const Duration(seconds: 4),
    this.animationDuration = const Duration(milliseconds: 400),
    this.comfortableMargin = 0.10,
    this.minScale = 0.5,
    this.maxScale = 2.0,
    this.forceAutoPan = false,
    this.latestContentHeadroom = 0.12,
    this.extentGrowthHeadroom = 0.18,
    TickerProvider? tickerProvider,
  }) : _animationController = tickerProvider != null
            ? AnimationController(
                vsync: tickerProvider,
                duration: animationDuration,
              )
            : null {
    _animationController?.addStatusListener(_handleAnimationStatus);
  }

  // ------------------------------------------------------------------ config --

  /// Viewport width (logical px) at or below which auto-pan is enabled.
  final double mobileBreakpoint;

  /// Cooldown after manual pan/zoom before automatic movement resumes.
  final Duration manualCooldown;

  /// Duration of animated viewport movement.
  final Duration animationDuration;

  /// Comfortable margin (fraction of viewport) kept around focused content.
  final double comfortableMargin;

  /// Minimum / maximum zoom scale.
  final double minScale;
  final double maxScale;

  /// When true, auto-pan is enabled regardless of viewport size.
  final bool forceAutoPan;

  /// Extra visible space kept below the newest content when auto-following.
  final double latestContentHeadroom;

  /// Extra virtual scroll space kept below the newest content.
  final double extentGrowthHeadroom;

  // ------------------------------------------------------------------- state --

  AnimationController? _animationController;
  Size? _viewportSize;
  BoardRect? _contentBounds;
  double _contentExtent = 1.0;
  Offset _normalizedOffset = Offset.zero;
  double _normalizedScale = 1.0;
  bool _followLatest = true;
  bool _manuallyInteracting = false;
  Timer? _cooldownTimer;
  bool _animationInProgress = false;
  Offset _animStartOffset = Offset.zero;
  Offset _animTargetOffset = Offset.zero;
  double _animStartScale = 1.0;
  double _animTargetScale = 1.0;

  // ------------------------------------------------------------------ getters --

  Size? get viewportSize => _viewportSize;
  BoardRect? get contentBounds => _contentBounds;
  double get contentExtent => _contentExtent;
  Offset get offset => _normalizedOffset;
  double get scale => _normalizedScale;
  bool get followLatest => _followLatest;

  bool get autoPanEnabled => forceAutoPan || isMobile;
  bool get isMobile =>
      (_viewportSize?.width ?? 0) > 0 &&
      _viewportSize!.width <= mobileBreakpoint;
  bool get isManuallyInteracting => _manuallyInteracting;
  bool get isAnimating => _animationInProgress;

  /// The normalized rectangle currently visible in the viewport.
  BoardRect get visibleRect => _visibleRectFor(_normalizedOffset, _normalizedScale);

  BoardRect _visibleRectFor(Offset offset, double scale) {
    final visibleW = (1.0 / scale).clamp(0.0, 1.0);
    final visibleH = (1.0 / scale).clamp(0.0, _contentExtent);
    final clamped = _clampOffsetValue(offset, scale: scale);
    return BoardRect(clamped.dx, clamped.dy, visibleW, visibleH);
  }

  // -------------------------------------------------------- configuration --

  void attachTickerProvider(TickerProvider provider) {
    if (_animationController != null) return;
    _animationController = AnimationController(
      vsync: provider,
      duration: animationDuration,
    )..addStatusListener(_handleAnimationStatus);
  }

  void _handleAnimationStatus(AnimationStatus status) {
    if (status == AnimationStatus.completed ||
        status == AnimationStatus.dismissed) {
      _animationInProgress = false;
      notifyListeners();
    }
  }

  void onViewportSizeChanged(Size size) {
    if (_viewportSize == size) return;
    _viewportSize = size;
    _normalizedOffset = _clampOffsetValue(_normalizedOffset);
    notifyListeners();
  }

  /// Notify the controller of the current normalized bounds of all visible
  /// board content. Used to clamp panning and to grow the virtual extent.
  void onContentBoundsChanged(BoardRect? bounds) {
    _contentBounds = bounds;
    final nextExtent = _extentForBounds(bounds);
    final changed = nextExtent != _contentExtent;
    _contentExtent = nextExtent;
    _normalizedOffset = _clampOffsetValue(_normalizedOffset);
    if (changed || bounds != null) {
      notifyListeners();
    }
  }

  double _extentForBounds(BoardRect? bounds) {
    if (bounds == null || bounds.isEmpty) return 1.0;
    final needed = bounds.bottom + extentGrowthHeadroom;
    return math.max(1.0, needed);
  }

  // -------------------------------------------------- manual interaction --

  /// Suspends automatic following. Manual pan/zoom always wins.
  void suspendFollow() {
    _followLatest = false;
    _cancelAnimation();
    _cooldownTimer?.cancel();
    _cooldownTimer = null;
  }

  /// Resumes automatic following without forcing an immediate jump.
  void resumeFollow({bool snapToLatest = false}) {
    _cooldownTimer?.cancel();
    _cooldownTimer = null;
    _manuallyInteracting = false;
    _followLatest = true;
    if (snapToLatest && _contentBounds != null && !_contentBounds!.isEmpty) {
      final target = _targetForLatestContent();
      _normalizedOffset = target.$1;
      _normalizedScale = target.$2;
      notifyListeners();
    } else {
      notifyListeners();
    }
  }

  void onManualInteractionStart() {
    suspendFollow();
    if (!_manuallyInteracting) {
      _manuallyInteracting = true;
      notifyListeners();
    } else {
      notifyListeners();
    }
  }

  void onManualInteractionEnd() {
    _startCooldown();
  }

  void _startCooldown() {
    _cooldownTimer?.cancel();
    _cooldownTimer = Timer(manualCooldown, () {
      _cooldownTimer = null;
      _manuallyInteracting = false;
      _followLatest = true;
      notifyListeners();
    });
  }

  /// Manually set the viewport offset and scale (e.g. from a gesture).
  /// Cancels any in-flight animation.
  void setViewport(Offset offset, double scale, {bool userInitiated = false}) {
    _cancelAnimation();
    if (userInitiated) {
      _followLatest = false;
      _manuallyInteracting = true;
      _startCooldown();
    }
    _normalizedScale = math.min(math.max(scale, minScale), maxScale);
    _normalizedOffset = _clampOffsetValue(offset);
    notifyListeners();
  }

  // ---------------------------------------------------------- auto-pan API --

  Future<void> focusOnElement(
    String elementId, {
    BoardDocument? document,
    String priority = 'normal',
    bool animate = true,
  }) {
    BoardRect? rect;
    if (document != null) {
      rect = document.boundsOf(
        <String>[elementId],
        verticalExtent: _contentExtent,
      );
    }
    if (rect == null || rect.isEmpty) {
      final element = document?[elementId];
      final b = element?.declaredBounds;
      if (b != null && !b.isEmpty) rect = b.clampToExtent(_contentExtent);
    }
    if (rect == null || rect.isEmpty) return Future<void>.value();
    return focusOnRect(rect, priority: priority, animate: animate);
  }

  Future<void> focusOnElements(
    List<String> elementIds, {
    BoardDocument? document,
    String priority = 'normal',
    bool animate = true,
  }) {
    if (elementIds.isEmpty) return Future<void>.value();
    BoardRect? rect;
    if (document != null) {
      rect = document.boundsOf(elementIds, verticalExtent: _contentExtent);
    }
    if (rect == null || rect.isEmpty) {
      BoardRect? union;
      for (final id in elementIds) {
        final b = document?[id]?.declaredBounds;
        if (b == null || b.isEmpty) continue;
        union = union == null ? b : union.union(b);
      }
      rect = union;
    }
    if (rect == null || rect.isEmpty) return Future<void>.value();
    return focusOnRect(rect, priority: priority, animate: animate);
  }

  Future<void> focusOnRect(
    BoardRect rect, {
    String priority = 'normal',
    bool animate = true,
  }) {
    if (!autoPanEnabled) return Future<void>.value();
    if (_manuallyInteracting) return Future<void>.value();
    if (rect.isEmpty) return Future<void>.value();
    if (_isComfortablyVisible(rect)) return Future<void>.value();
    final target = _computeTarget(rect);
    return _animateTo(target.$1, target.$2, animate: animate);
  }

  /// Auto-follow newest content when following is active. Keeps the latest
  /// content visible with space below and never jumps if content is already
  /// comfortably visible.
  Future<void> followLatestContent({
    BoardRect? contentBounds,
    bool animate = true,
  }) {
    if (contentBounds != null) onContentBoundsChanged(contentBounds);
    if (!_followLatest || _manuallyInteracting || !autoPanEnabled) {
      return Future<void>.value();
    }
    final bounds = contentBounds ?? _contentBounds;
    if (bounds == null || bounds.isEmpty) return Future<void>.value();
    final target = _targetForLatestContent(bounds: bounds);
    final targetRect = BoardRect(
      bounds.x,
      math.max(0.0, bounds.bottom - _visibleRectFor(target.$1, target.$2).h * 0.35),
      bounds.w,
      math.min(bounds.h, _visibleRectFor(target.$1, target.$2).h * 0.35),
    );
    if (_isComfortablyVisible(targetRect)) return Future<void>.value();
    return _animateTo(target.$1, target.$2, animate: animate);
  }

  bool isComfortablyVisible(BoardRect rect) => _isComfortablyVisible(rect);

  // ---------------------------------------------------------- animation --

  Future<void> _animateTo(
    Offset targetOffset,
    double targetScale, {
    bool animate = true,
  }) {
    if (_manuallyInteracting) return Future<void>.value();

    targetScale = math.min(math.max(targetScale, minScale), maxScale);
    final clampedTarget = _clampOffsetValue(targetOffset, scale: targetScale);

    final controller = _animationController;
    if (!animate || controller == null) {
      _normalizedOffset = clampedTarget;
      _normalizedScale = targetScale;
      _animationInProgress = false;
      notifyListeners();
      return Future<void>.value();
    }

    _animationInProgress = true;
    _animStartOffset = _normalizedOffset;
    _animStartScale = _normalizedScale;
    _animTargetOffset = clampedTarget;
    _animTargetScale = targetScale;
    controller.removeListener(_tickAnimation);
    controller.addListener(_tickAnimation);

    controller
      ..value = 0.0;
    return controller.forward().then((_) {
      controller.removeListener(_tickAnimation);
      _animationInProgress = false;
    });
  }

  void _tickAnimation() {
    final controller = _animationController;
    if (controller == null) return;
    final t = Curves.easeOutCubic.transform(controller.value.clamp(0.0, 1.0));
    _normalizedOffset = Offset.lerp(_animStartOffset, _animTargetOffset, t)!;
    _normalizedScale = _animStartScale + (_animTargetScale - _animStartScale) * t;
    _normalizedOffset = _clampOffsetValue(_normalizedOffset);
    notifyListeners();
  }

  void _cancelAnimation() {
    _animationController?.removeListener(_tickAnimation);
    _animationController?.stop(canceled: true);
    _animationInProgress = false;
  }

  // ----------------------------------------------------------- clamping --

  void _clampOffset({Offset? target}) {
    _normalizedOffset = _clampOffsetValue(target ?? _normalizedOffset);
  }

  Offset _clampOffsetValue(Offset offset, {double? scale}) {
    final effectiveScale = math.min(math.max(scale ?? _normalizedScale, minScale), maxScale);
    final visibleW = (1.0 / effectiveScale).clamp(0.0, 1.0);
    final visibleH = (1.0 / effectiveScale).clamp(0.0, _contentExtent);

    var minX = 0.0;
    var minY = 0.0;
    var maxX = (1.0 - visibleW).clamp(0.0, 1.0);
    var maxY = (_contentExtent - visibleH).clamp(0.0, _contentExtent);

    final bounds = _contentBounds;
    if (bounds != null && !bounds.isEmpty) {
      final comfortX = visibleW * 0.1;
      final comfortY = visibleH * 0.1;
      minX = math.min(minX, math.max(0.0, bounds.x - comfortX));
      minY = math.min(minY, math.max(0.0, bounds.y - comfortY));
      maxX = math.max(maxX, math.min(maxX, bounds.right - visibleW + comfortX));
      maxY = math.max(maxY, math.min(maxY, bounds.bottom - visibleH + comfortY));
      maxX = maxX.clamp(0.0, (1.0 - visibleW).clamp(0.0, 1.0));
      maxY = maxY.clamp(0.0, (_contentExtent - visibleH).clamp(0.0, _contentExtent));
      if (maxX < minX) maxX = minX;
      if (maxY < minY) maxY = minY;
    }

    return Offset(offset.dx.clamp(minX, maxX), offset.dy.clamp(minY, maxY));
  }

  bool _isComfortablyVisible(BoardRect rect) {
    final visible = visibleRect;
    final marginX = comfortableMargin * visible.w;
    final marginY = comfortableMargin * visible.h;
    return rect.x >= visible.x - marginX &&
        rect.y >= visible.y - marginY &&
        rect.right <= visible.right + marginX &&
        rect.bottom <= visible.bottom + marginY;
  }

  (Offset, double) _computeTarget(BoardRect rect) {
    final margin = comfortableMargin;
    final scaleForWidth = (1.0 - 2 * margin) / math.max(rect.w, 0.001);
    final scaleForHeight = (1.0 - 2 * margin) / math.max(rect.h, 0.001);
    var targetScale = math.min(scaleForWidth, scaleForHeight);
    targetScale = targetScale.clamp(minScale, maxScale);
    final effectiveScale = math.max(_normalizedScale, targetScale);

    final visibleW = (1.0 / effectiveScale).clamp(0.0, 1.0);
    final visibleH = (1.0 / effectiveScale).clamp(0.0, _contentExtent);

    final targetOffset = Offset(
      rect.centerX - visibleW / 2,
      rect.centerY - visibleH / 2,
    );
    return (_clampOffsetValue(targetOffset, scale: effectiveScale), effectiveScale);
  }

  (Offset, double) _targetForLatestContent({BoardRect? bounds}) {
    final rect = bounds ?? _contentBounds;
    final effectiveScale = _normalizedScale.clamp(minScale, maxScale);
    final visibleW = (1.0 / effectiveScale).clamp(0.0, 1.0);
    final visibleH = (1.0 / effectiveScale).clamp(0.0, _contentExtent);
    if (rect == null || rect.isEmpty) {
      return (_clampOffsetValue(_normalizedOffset, scale: effectiveScale), effectiveScale);
    }
    final bottom = rect.bottom + latestContentHeadroom * visibleH;
    final targetY = bottom - visibleH;
    final targetX = rect.centerX - visibleW / 2;
    return (
      _clampOffsetValue(Offset(targetX, targetY), scale: effectiveScale),
      effectiveScale,
    );
  }

  @override
  void dispose() {
    _cancelAnimation();
    _cooldownTimer?.cancel();
    _animationController?.dispose();
    super.dispose();
  }
}

extension BoardViewportBounds on BoardDocument {
  BoardRect? get allVisibleBounds {
    BoardRect? union;
    for (final element in visibleElements) {
      final b = element.declaredBounds;
      if (b == null || b.isEmpty) continue;
      union = union == null ? b : union.union(b);
    }
    return union;
  }
}
