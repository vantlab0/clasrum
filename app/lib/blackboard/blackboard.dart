/// Blackboard module public barrel (CONTRACTS §10).
///
/// Exports exactly: `BoardEvent`, `BoardDocument`, `BlackboardController`,
/// `BlackboardView` plus the supporting model/render types consumers need.

export 'model/board_document.dart';
export 'model/board_element.dart';
export 'model/board_event.dart';
export 'model/board_geometry.dart';
export 'model/board_metrics.dart';
export 'model/chalk_palette.dart';
export 'runtime/animation_scheduler.dart';
export 'runtime/blackboard_controller.dart';
export 'runtime/event_queue.dart';
export 'render/chalk_text.dart';
export 'render/board_render_cache.dart' hide BoardBoundsRegistry;

/// The tutorial board widget from CONTRACTS §10.
///
/// ```dart
/// BlackboardView(
///   controller: controller,
///   interactive: true,
/// )
/// ```
export 'blackboard_view.dart';
