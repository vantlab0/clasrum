import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/animation.dart';
import 'package:flutter/foundation.dart';

import 'dart:ui' show Offset, Size;

import '../model/board_document.dart';
import '../model/board_geometry.dart';

/// Intelligent blackboard viewport controller (Phase 3).
///
/// Provides smooth auto-pan / zoom so that important board content (new
/// elements, highlighted elements, groups of elements) enters the comfortable
/// viewing area without requiring the student to drag the board manually.
///
/// Design
/// ------
/// * Mobile-first: auto-pan is active by default when the viewport width is at
///   or below [mobileBreakpoint] (600 logical px). On tablets/desktops it is
///   disabled unless [forceAutoPan] is set.
/// * Manual interaction always wins: when the student pans or zooms, automatic
///   movement is suspended for [manualCooldown] (default 4 s).
/// * Deterministic, local math: no AI calls, no external dependencies.
/// * Smooth, interruptible animation via a single [AnimationController].
/// * Coordinates are normalized board space (0..1, origin top-left).
///
/// The controller does not own a [Widget]; it exposes an offset and scale that
/// the consumer (typically [BlackboardView] via [BoardTransform.withViewport])
/// applies to the render transform. It is a [ChangeNotifier] so consumers can
/// repaint on change.
class BoardViewportController extends ChangeNotifier {
  BoardViewportController({
    this.mobileBreakpoint = 600.0,
    this.manualCooldown = const Duration(seconds: 4),
    this.animationDuration = const Duration(milliseconds: 400),
    this.comfortableMargin = 0.10,
    this.minScale = 0.5,
    this.maxScale = 2.0,
    this.forceAutoPan = false,
    TickerProvider? tickerProvider,
  }) : _animationController = tickerProvider != null
            ? AnimationController(
                vsync: tickerProvider,
                duration: animationDuration,
              )
            : null {
    _animationController?.addStatusListener((status) {
      if (status == AnimationStatus.completed ||
          status == AnimationStatus.dismissed) {
        _animationInProgress = false;
        notifyListeners();
      }
    });
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

  // ------------------------------------------------------------------- state --

  AnimationController? _animationController;

  Size? _viewportSize; // logical pixels
  BoardRect? _contentBounds; // normalized board bounds of all visible content
  Offset _normalizedOffset =
      Offset.zero; // top-left of visible area in board coords
  double _normalizedScale = 1.0;

  bool _manuallyInteracting = false;
  Timer? _cooldownTimer;
  bool _animationInProgress = false;

  // ------------------------------------------------------------------ getters --

  /// Viewport size in logical pixels.
  Size? get viewportSize => _viewportSize;

  /// Normalized bounds of all visible board content.
  BoardRect? get contentBounds => _contentBounds;

  /// Normalized offset (top-left corner of visible area in board coords).
  Offset get offset => _normalizedOffset;

  /// Current zoom scale.
  double get scale => _normalizedScale;

  /// True when auto-pan is active (mobile by default, or forced).
  bool get autoPanEnabled => forceAutoPan || isMobile;

  /// True when the viewport is narrow enough to be considered mobile.
  bool get isMobile =>
      (_viewportSize?.width ?? 0) > 0 &&
      _viewportSize!.width <= mobileBreakpoint;

  /// True while the student is manually panning/zooming (auto-pan suspended).
  bool get isManuallyInteracting => _manuallyInteracting;

  /// True while an animated pan is in flight.
  bool get isAnimating => _animationInProgress;

  /// The normalized rectangle currently visible in the viewport.
  BoardRect get visibleRect {
    final visibleW = (1.0 / _normalizedScale).clamp(0.0, 1.0);
    final visibleH = (1.0 / _normalizedScale).clamp(0.0, 1.0);
    return BoardRect(
      _normalizedOffset.dx.clamp(0.0, (1.0 - visibleW).clamp(0.0, 1.0)),
      _normalizedOffset.dy.clamp(0.0, (1.0 - visibleH).clamp(0.0, 1.0)),
      visibleW,
      visibleH,
    ).clamp01();
  }

  // -------------------------------------------------------- configuration --

  /// Attach a [TickerProvider] (e.g. a State with `TickerProviderStateMixin`)
  /// so animated panning can run. Call once during init.
  void attachTickerProvider(TickerProvider provider) {
    if (_animationController != null) return;
    _animationController = AnimationController(
      vsync: provider,
      duration: animationDuration,
    )..addStatusListener((status) {
        if (status == AnimationStatus.completed ||
            status == AnimationStatus.dismissed) {
          _animationInProgress = false;
          notifyListeners();
        }
      });
  }

  /// Notify the controller that the viewport (widget) size changed.
  void onViewportSizeChanged(Size size) {
    if (_viewportSize == size) return;
    _viewportSize = size;
    notifyListeners();
  }

  /// Notify the controller of the current normalized bounds of all visible
  /// board content. Used to clamp panning so the viewport never shows empty
  /// space beyond the board.
  void onContentBoundsChanged(BoardRect? bounds) {
    _contentBounds = bounds;
    _clampOffset();
    notifyListeners();
  }

  // -------------------------------------------------- manual interaction --

  /// Call when the student begins a manual pan or zoom. Suspends auto-pan and
  /// starts the cooldown.
  void onManualInteractionStart() {
    _cancelAnimation();
    if (!_manuallyInteracting) {
      _manuallyInteracting = true;
      notifyListeners();
    }
    _startCooldown();
  }

  /// Call when the student ends a manual pan or zoom. Starts the cooldown.
  void onManualInteractionEnd() {
    _startCooldown();
  }

  void _startCooldown() {
    _cooldownTimer?.cancel();
    _cooldownTimer = Timer(manualCooldown, () {
      _cooldownTimer = null;
      _manuallyInteracting = false;
      notifyListeners();
    });
  }

  /// Manually set the viewport offset and scale (e.g. from a gesture).
  /// Cancels any in-flight animation.
  void setViewport(Offset offset, double scale) {
    _cancelAnimation();
    _normalizedOffset = offset;
    _normalizedScale = math.min(math.max(scale, minScale), maxScale);
    _clampOffset();
    notifyListeners();
  }

  // ---------------------------------------------------------- auto-pan API --

  /// Focus the viewport on a board element. Computes the union bounds of the
  /// target element(s) and pans so they are comfortably visible.
  ///
  /// Returns when the animation completes (or immediately if auto-pan is
  /// disabled or manual interaction is in progress).
  Future<void> focusOnElement(
    String elementId, {
    BoardDocument? document,
    String priority = 'normal',
    bool animate = true,
  }) {
    BoardRect? rect;
    if (document != null) {
      rect = document.boundsOf(<String>[elementId]);
    }
    if (rect == null || rect.isEmpty) {
      // Fall back to element position/size if document bounds unavailable.
      final element = document?[elementId];
      if (element != null && element.declaredBounds != null) {
        rect = element.declaredBounds!;
      }
    }
    if (rect == null || rect.isEmpty) {
      return Future<void>.value();
    }
    return focusOnRect(rect!, priority: priority, animate: animate);
  }

  /// Focus the viewport on a list of board elements (their union bounds).
  Future<void> focusOnElements(
    List<String> elementIds, {
    BoardDocument? document,
    String priority = 'normal',
    bool animate = true,
  }) {
    if (elementIds.isEmpty) return Future<void>.value();
    BoardRect? rect;
    if (document != null) {
      rect = document.boundsOf(elementIds);
    }
    if (rect == null || rect.isEmpty) {
      // Fall back to union of declared bounds.
      rect = BoardRect.zero;
      var first = true;
      for (final id in elementIds) {
        final element = document?[id];
        final b = element?.declaredBounds;
        if (b != null && !b.isEmpty) {
          rect = first ? b : rect!.union(b);
          first = false;
        }
      }
      if (first) return Future<void>.value();
    }
    return focusOnRect(rect!, priority: priority, animate: animate);
  }

  /// Focus the viewport on an arbitrary normalized board rect.
  ///
  /// If the content is already comfortably visible, no movement occurs.
  Future<void> focusOnRect(
    BoardRect rect, {
    String priority = 'normal',
    bool animate = true,
  }) {
    if (!autoPanEnabled) return Future<void>.value();
    if (_manuallyInteracting) return Future<void>.value();
    if (rect.isEmpty) return Future<void>.value();

    // Already comfortably visible? Don't move.
    if (_isComfortablyVisible(rect)) return Future<void>.value();

    final (Offset targetOffset, double targetScale) = _computeTarget(rect);
    return _animateTo(targetOffset, targetScale, animate: animate);
  }

  /// Return true if [rect] is already comfortably visible in the current
  /// viewport (respecting [comfortableMargin]).
  bool isComfortablyVisible(BoardRect rect) => _isComfortablyVisible(rect);

  // ---------------------------------------------------------- animation --

  Future<void> _animateTo(
    Offset targetOffset,
    double targetScale, {
    bool animate = true,
  }) {
    if (_manuallyInteracting) return Future<void>.value();

    targetScale = math.min(math.max(targetScale, minScale), maxScale);
    _clampOffset(target: targetOffset);

    final controller = _animationController;
    if (!animate || controller == null) {
      // Instant apply.
      _normalizedOffset = targetOffset;
      _normalizedScale = targetScale;
      _animationInProgress = false;
      notifyListeners();
      return Future<void>.value();
    }

    _animationInProgress = true;
    final startOffset = _normalizedOffset;
    final startScale = _normalizedScale;

    controller
      ..value = 0.0
      ..addListener(() {
        final t = controller.value;
        _normalizedOffset = Offset.lerp(startOffset, targetOffset, t)!;
        _normalizedScale = startScale + (targetScale - startScale) * t;
        _clampOffset();
        notifyListeners();
      });

    return controller.forward().then((_) {
      _animationInProgress = false;
    });
  }

  void _cancelAnimation() {
    _animationController?.stop(canceled: true);
    _animationInProgress = false;
  }

  // ----------------------------------------------------------- clamping --

  void _clampOffset({Offset? target}) {
    final offset = target ?? _normalizedOffset;
    final scale = _normalizedScale;
    final visibleW = (1.0 / scale).clamp(0.0, 1.0);
    final visibleH = (1.0 / scale).clamp(0.0, 1.0);

    // Clamp so we never pan past the board content bounds (if known).
    double minX = 0.0;
    double minY = 0.0;
    double maxX = (1.0 - visibleW).clamp(0.0, 1.0);
    double maxY = (1.0 - visibleH).clamp(0.0, 1.0);

    if (_contentBounds != null && !_contentBounds!.isEmpty) {
      // Don't show empty space beyond content; clamp to content edges.
      final cb = _contentBounds!;
      minX = math.max(0.0, cb.x - visibleW * 0.1);
      minY = math.max(0.0, cb.y - visibleH * 0.1);
      maxX = math.min(maxX, cb.right.clamp(0.0, 1.0) - visibleW * 0.1);
      maxY = math.min(maxY, cb.bottom.clamp(0.0, 1.0) - visibleH * 0.1);
      if (maxX < minX) maxX = minX = (minX + maxX) / 2;
      if (maxY < minY) maxY = minY = (minY + maxY) / 2;
    }

    if (target != null) {
      _normalizedOffset = Offset(
        offset.dx.clamp(minX, maxX),
        offset.dy.clamp(minY, maxY),
      );
    } else {
      _normalizedOffset = Offset(
        _normalizedOffset.dx.clamp(minX, maxX),
        _normalizedOffset.dy.clamp(minY, maxY),
      );
    }
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

  /// Compute the offset + scale that brings [rect] into comfortable view.
  (Offset, double) _computeTarget(BoardRect rect) {
    final margin = comfortableMargin;
    final scaleForWidth = (1.0 - 2 * margin) / rect.w;
    final scaleForHeight = (1.0 - 2 * margin) / rect.h;
    var targetScale = math.min(scaleForWidth, scaleForHeight);
    targetScale = targetScale.clamp(minScale, maxScale);

    // Don't zoom in further than necessary if already close.
    final effectiveScale =
        targetScale > _normalizedScale ? targetScale : _normalizedScale;

    final visibleW = (1.0 / effectiveScale).clamp(0.0, 1.0);
    final visibleH = (1.0 / effectiveScale).clamp(0.0, 1.0);

    var targetOffsetX = rect.centerX - visibleW / 2;
    var targetOffsetY = rect.centerY - visibleH / 2;

    targetOffsetX = targetOffsetX.clamp(
      0.0,
      (1.0 - visibleW).clamp(0.0, 1.0),
    );
    targetOffsetY = targetOffsetY.clamp(
      0.0,
      (1.0 - visibleH).clamp(0.0, 1.0),
    );

    return (Offset(targetOffsetX, targetOffsetY), effectiveScale);
  }

  @override
  void dispose() {
    _cancelAnimation();
    _cooldownTimer?.cancel();
    _animationController?.dispose();
    super.dispose();
  }
}

/// Mix-in convenience: board element bounds union for a document.
extension BoardViewportBounds on BoardDocument {
  /// Normalized union bounds of all visible elements (for viewport clamping).
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
