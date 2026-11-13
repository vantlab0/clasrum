import 'package:flutter/material.dart';

/// Clasrum premium UI palette — strict design system.
/// Blackboard/chalk rendering uses its own palette in `blackboard/model`.
abstract final class AppColors {
  // Required system colors
  static const Color blue = Color(0xFF1677FF); // primary actions / selected
  static const Color background = Color(0xFF080A0E); // main background
  static const Color surface = Color(0xFF12161D); // solid dark surface
  static const Color white = Color(0xFFFFFFFF); // primary text/icons
  static const Color muted = Color(0xFFA1AAB8); // secondary text
  static const Color border = Color(0xFF303743); // subtle borders

  // Compatibility aliases used across the app
  static const Color chalk = white;
  static const Color chalkMuted = muted;
  static const Color outline = border;
  static const Color outlineSoft = border;
  static const Color surfaceRaised = surface;
  static const Color surfaceHigh = Color(0xFF171C24);
  static const Color board = Color(0xFF171817);

  // Kept for existing status/error usages (use sparingly)
  static const Color red = Color(0xFFE05555);
}

abstract final class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
}

abstract final class AppRadius {
  static const double sm = 10;
  static const double md = 14;
  static const double lg = 20;
  static const double xl = 28;
}

abstract final class AppTheme {
  static ThemeData dark() {
    final base = ThemeData(brightness: Brightness.dark, useMaterial3: true);
    const scheme = ColorScheme.dark(
      primary: AppColors.blue,
      onPrimary: AppColors.white,
      secondary: AppColors.blue,
      onSecondary: AppColors.white,
      surface: AppColors.background,
      onSurface: AppColors.white,
      onSurfaceVariant: AppColors.muted,
      outline: AppColors.border,
      outlineVariant: AppColors.border,
      error: AppColors.red,
      onError: AppColors.white,
    );

    final textTheme = base.textTheme
        .copyWith(
          displaySmall: _display(32),
          headlineLarge: _display(28),
          headlineMedium: _display(24),
          headlineSmall: _title(22, FontWeight.w600),
          titleLarge: _title(19, FontWeight.w600),
          titleMedium: _title(16, FontWeight.w600),
          titleSmall: _title(14, FontWeight.w600),
          bodyLarge: _body(16),
          bodyMedium: _body(14),
          bodySmall: _body(12, AppColors.muted),
          labelLarge: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.2,
          ),
          labelMedium: _body(12, AppColors.muted),
        )
        .apply(bodyColor: AppColors.white, displayColor: AppColors.white);

    final inputBorder = OutlineInputBorder(
      borderRadius: BorderRadius.circular(AppRadius.lg),
      borderSide: BorderSide(color: AppColors.border.withValues(alpha: 0.7)),
    );

    return base.copyWith(
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.background,
      canvasColor: AppColors.surface,
      textTheme: textTheme,
      dividerTheme: DividerThemeData(
        color: AppColors.border.withValues(alpha: 0.6),
        thickness: 1,
        space: AppSpacing.lg,
      ),
      appBarTheme: AppBarThemeData(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        foregroundColor: AppColors.white,
        titleTextStyle: textTheme.titleLarge,
        iconTheme: const IconThemeData(color: AppColors.white, size: 22),
      ),
      cardTheme: CardThemeData(
        color: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.lg),
          side: BorderSide(color: AppColors.border.withValues(alpha: 0.7)),
        ),
      ),
      inputDecorationTheme: InputDecorationThemeData(
        filled: true,
        fillColor: AppColors.surface.withValues(alpha: 0.65),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 18, vertical: 16),
        hintStyle:
            textTheme.bodyMedium?.copyWith(color: AppColors.muted.withValues(alpha: 0.9)),
        labelStyle: textTheme.bodyMedium?.copyWith(color: AppColors.muted),
        prefixIconColor: AppColors.muted,
        suffixIconColor: AppColors.muted,
        border: inputBorder,
        enabledBorder: inputBorder,
        focusedBorder: inputBorder.copyWith(
          borderSide: const BorderSide(color: AppColors.blue, width: 1.4),
        ),
        errorBorder: inputBorder.copyWith(
          borderSide: const BorderSide(color: AppColors.red),
        ),
        focusedErrorBorder: inputBorder.copyWith(
          borderSide: const BorderSide(color: AppColors.red, width: 1.4),
        ),
        errorStyle: const TextStyle(fontSize: 12, color: AppColors.red),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.blue,
          foregroundColor: AppColors.white,
          minimumSize: const Size(64, 48),
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
          textStyle: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.2,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
          ),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.white,
          minimumSize: const Size(64, 44),
          side: BorderSide(color: AppColors.border.withValues(alpha: 0.8)),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.white,
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: AppColors.white,
          iconSize: 22,
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: AppColors.surfaceHigh,
        contentTextStyle: textTheme.bodyMedium,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
          side: BorderSide(color: AppColors.border.withValues(alpha: 0.6)),
        ),
        actionTextColor: AppColors.blue,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: textTheme.titleLarge,
        contentTextStyle:
            textTheme.bodyMedium?.copyWith(color: AppColors.muted, height: 1.5),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.lg),
          side: BorderSide(color: AppColors.border.withValues(alpha: 0.7)),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: AppColors.surface,
        modalBackgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        dragHandleColor: AppColors.border,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.xl)),
        ),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.blue,
        linearTrackColor: AppColors.surfaceHigh,
        circularTrackColor: AppColors.surfaceHigh,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith<Color?>((states) {
          if (states.contains(WidgetState.selected)) return AppColors.white;
          return AppColors.muted;
        }),
        trackColor: WidgetStateProperty.resolveWith<Color?>((states) {
          if (states.contains(WidgetState.selected)) return AppColors.blue;
          return AppColors.surfaceHigh;
        }),
        trackOutlineColor:
            const WidgetStatePropertyAll<Color>(Colors.transparent),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.surface.withValues(alpha: 0.7),
        selectedColor: AppColors.blue.withValues(alpha: 0.18),
        side: BorderSide(color: AppColors.border.withValues(alpha: 0.7)),
        labelStyle: textTheme.bodySmall?.copyWith(color: AppColors.white),
        secondaryLabelStyle:
            textTheme.bodySmall?.copyWith(color: AppColors.white),
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.sm,
        ),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(999)),
        ),
        showCheckmark: false,
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: AppColors.surfaceHigh,
          borderRadius: BorderRadius.circular(AppRadius.sm),
          border: Border.all(color: AppColors.border.withValues(alpha: 0.7)),
        ),
        textStyle: textTheme.bodySmall,
        waitDuration: const Duration(milliseconds: 500),
      ),
      listTileTheme: const ListTileThemeData(
        iconColor: AppColors.muted,
        textColor: AppColors.white,
      ),
    );
  }

  static TextStyle _display(double size) => TextStyle(
        fontSize: size,
        height: 1.15,
        fontWeight: FontWeight.w600,
        letterSpacing: -0.3,
        color: AppColors.white,
      );

  static TextStyle _title(double size, FontWeight weight) => TextStyle(
        fontSize: size,
        height: 1.25,
        fontWeight: weight,
        letterSpacing: -0.1,
        color: AppColors.white,
      );

  static TextStyle _body(double size, [Color color = AppColors.white]) =>
      TextStyle(
        fontSize: size,
        height: 1.5,
        color: color,
      );
}
