import 'package:flutter/material.dart';

/// Fixed chalk palette shared with the blackboard renderer (CONTRACTS §1).
abstract final class AppColors {
  static const Color background = Color(0xFF101215);
  static const Color surface = Color(0xFF171A1F);
  static const Color surfaceRaised = Color(0xFF1E2229);
  static const Color surfaceHigh = Color(0xFF262C35);
  static const Color outline = Color(0xFF3C434E);
  static const Color outlineSoft = Color(0xFF2B3039);

  /// Default chalk cream — primary text and primary action.
  static const Color chalk = Color(0xFFF2F0E8);

  /// Muted chalk — secondary text.
  static const Color chalkMuted = Color(0xFFA8A79E);

  static const Color yellow = Color(0xFFE8D58C);
  static const Color blue = Color(0xFF9CC9E3);
  static const Color green = Color(0xFFA9D3A0);
  static const Color red = Color(0xFFE39C9C);
  static const Color violet = Color(0xFFC9B8E8);

  static const Color board = Color(0xFF14171B);
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
    final scheme = ColorScheme.fromSeed(
      seedColor: AppColors.yellow,
      brightness: Brightness.dark,
    ).copyWith(
      primary: AppColors.chalk,
      onPrimary: const Color(0xFF15171B),
      primaryContainer: AppColors.surfaceHigh,
      onPrimaryContainer: AppColors.chalk,
      secondary: AppColors.yellow,
      onSecondary: const Color(0xFF201D12),
      tertiary: AppColors.blue,
      onTertiary: const Color(0xFF10202A),
      error: AppColors.red,
      onError: const Color(0xFF2A1515),
      surface: AppColors.background,
      onSurface: AppColors.chalk,
      onSurfaceVariant: AppColors.chalkMuted,
      surfaceContainerLowest: AppColors.background,
      surfaceContainerLow: AppColors.surface,
      surfaceContainer: AppColors.surface,
      surfaceContainerHigh: AppColors.surfaceRaised,
      surfaceContainerHighest: AppColors.surfaceHigh,
      outline: AppColors.outline,
      outlineVariant: AppColors.outlineSoft,
      inverseSurface: AppColors.chalk,
      onInverseSurface: const Color(0xFF15171B),
    );

    final textTheme = base.textTheme.copyWith(
      displaySmall: _display(34),
      headlineLarge: _display(30),
      headlineMedium: _display(26),
      headlineSmall: _chalk(22, FontWeight.w600),
      titleLarge: _chalk(19, FontWeight.w600),
      titleMedium: _chalk(16, FontWeight.w600),
      titleSmall: _chalk(14, FontWeight.w600),
      bodyLarge: _body(16),
      bodyMedium: _body(14),
      bodySmall: _body(12, AppColors.chalkMuted),
      labelLarge: const TextStyle(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.2,
      ),
      labelMedium: _body(12, AppColors.chalkMuted),
    ).apply(
      bodyColor: AppColors.chalk,
      displayColor: AppColors.chalk,
    );

    const inputBorder = OutlineInputBorder(
      borderRadius: BorderRadius.all(Radius.circular(AppRadius.md)),
      borderSide: BorderSide(color: AppColors.outlineSoft),
    );

    return base.copyWith(
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.background,
      textTheme: textTheme,
      canvasColor: AppColors.surface,
      dividerTheme: const DividerThemeData(
        color: AppColors.outlineSoft,
        thickness: 1,
        space: AppSpacing.lg,
      ),
      appBarTheme: AppBarThemeData(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        foregroundColor: AppColors.chalk,
        titleTextStyle: textTheme.titleLarge,
        iconTheme: const IconThemeData(color: AppColors.chalk),
      ),
      cardTheme: const CardThemeData(
        color: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(AppRadius.lg)),
          side: BorderSide(color: AppColors.outlineSoft),
        ),
      ),
      inputDecorationTheme: InputDecorationThemeData(
        filled: true,
        fillColor: AppColors.surfaceRaised,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.lg,
        ),
        hintStyle: textTheme.bodyMedium?.copyWith(
          color: AppColors.chalkMuted.withValues(alpha: 0.8),
        ),
        prefixIconColor: AppColors.chalkMuted,
        suffixIconColor: AppColors.chalkMuted,
        border: inputBorder,
        enabledBorder: inputBorder,
        focusedBorder: inputBorder.copyWith(
          borderSide: const BorderSide(color: AppColors.chalk, width: 1.4),
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
          backgroundColor: AppColors.chalk,
          foregroundColor: const Color(0xFF15171B),
          minimumSize: const Size(64, 52),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          textStyle: const TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.2,
          ),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(AppRadius.md)),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.chalk,
          minimumSize: const Size(64, 48),
          side: const BorderSide(color: AppColors.outline),
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(AppRadius.md)),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.yellow,
          textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: AppColors.chalk.withValues(alpha: 0.14),
        height: 68,
        labelTextStyle: const WidgetStatePropertyAll<TextStyle>(
          TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
        ),
        iconTheme: WidgetStateProperty.resolveWith<IconThemeData>((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 24,
            color: selected ? AppColors.chalk : AppColors.chalkMuted,
          );
        }),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: AppColors.surfaceHigh,
        contentTextStyle: textTheme.bodyMedium,
        behavior: SnackBarBehavior.floating,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(AppRadius.md)),
        ),
        actionTextColor: AppColors.yellow,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: AppColors.surfaceRaised,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: textTheme.titleLarge,
        contentTextStyle: textTheme.bodyMedium,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(AppRadius.lg)),
          side: BorderSide(color: AppColors.outlineSoft),
        ),
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: AppColors.surface,
        modalBackgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        dragHandleColor: AppColors.outline,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.xl)),
        ),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.yellow,
        linearTrackColor: AppColors.surfaceHigh,
        circularTrackColor: AppColors.surfaceHigh,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith<Color?>((states) {
          if (states.contains(WidgetState.selected)) return const Color(0xFF15171B);
          return AppColors.chalkMuted;
        }),
        trackColor: WidgetStateProperty.resolveWith<Color?>((states) {
          if (states.contains(WidgetState.selected)) return AppColors.yellow;
          return AppColors.surfaceHigh;
        }),
        trackOutlineColor: const WidgetStatePropertyAll<Color>(Colors.transparent),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: AppColors.surfaceRaised,
        selectedColor: AppColors.chalk.withValues(alpha: 0.16),
        side: const BorderSide(color: AppColors.outlineSoft),
        labelStyle: textTheme.bodySmall?.copyWith(color: AppColors.chalk),
        secondaryLabelStyle: textTheme.bodySmall?.copyWith(
          color: AppColors.chalk,
        ),
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
          border: Border.all(color: AppColors.outlineSoft),
        ),
        textStyle: textTheme.bodySmall,
        waitDuration: const Duration(milliseconds: 500),
      ),
    );
  }

  /// Handwritten display face (bundled chalk font, falls back to the platform
  /// font if the asset is unavailable).
  static TextStyle _display(double size) => TextStyle(
        fontFamily: 'Chalkboard',
        fontSize: size,
        height: 1.12,
        fontWeight: FontWeight.w400,
        letterSpacing: 0.2,
        color: AppColors.chalk,
      );

  static TextStyle _chalk(double size, FontWeight weight) => TextStyle(
        fontFamily: 'Chalkboard',
        fontSize: size,
        height: 1.2,
        fontWeight: weight,
        color: AppColors.chalk,
      );

  static TextStyle _body(double size, [Color color = AppColors.chalk]) =>
      TextStyle(
        fontSize: size,
        height: 1.45,
        color: color,
      );
}
