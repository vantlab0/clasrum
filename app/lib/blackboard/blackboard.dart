/// Blackboard module public barrel (CONTRACTS §10).
///
/// Exports exactly: `BoardEvent`, `BoardDocument`, `BlackboardController`,
/// `BlackboardView` plus the supporting model/render types consumers need.
library;

export 'blackboard_view.dart';
export 'model/board_collision.dart';
export 'model/board_document.dart';
export 'model/board_element.dart';
export 'model/board_event.dart';
export 'model/board_geometry.dart';
export 'model/board_metrics.dart';
export 'model/chalk_palette.dart';
export 'model/drawing_plan.dart';
export 'render/board_render_cache.dart' hide BoardBoundsRegistry;
export 'render/chalk_text.dart';
export 'runtime/animation_scheduler.dart';
export 'runtime/blackboard_controller.dart';
export 'runtime/board_fullness_detector.dart';
export 'runtime/board_vertical_layout.dart';
export 'runtime/event_queue.dart';
