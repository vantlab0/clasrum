import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../core/widgets/clasrum_icons.dart';
import '../../core/widgets/glass_widgets.dart';

/// Premium upgrade screen.
///
/// No payment/subscription integration exists in the current app configuration.
/// This screen presents the upgrade entry point without simulating purchases or
/// inventing plan/pricing/subscription status.
class UpgradeScreen extends StatelessWidget {
  const UpgradeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        leading: IconButton(
          onPressed: () => Navigator.of(context).maybePop(),
          icon: const ClasrumIcon(ClasrumIconKind.back, size: 20),
          tooltip: 'Back',
        ),
        title: const Text('Upgrade to Pro'),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
          children: <Widget>[
            GlassContainer(
              radius: 22,
              padding: const EdgeInsets.all(20),
              blurSigma: 12,
              backgroundOpacity: 0.38,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: AppColors.blue.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(
                        color: AppColors.blue.withValues(alpha: 0.35),
                      ),
                    ),
                    child: const Center(
                      child: ClasrumIcon(
                        ClasrumIconKind.upgradePro,
                        size: 22,
                        color: AppColors.white,
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  const Text(
                    'Clasrum Pro',
                    style: TextStyle(
                      color: AppColors.white,
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Premium plans and upgrades will appear here when available.',
                    style: TextStyle(
                      color: AppColors.muted,
                      fontSize: 14,
                      height: 1.5,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            GlassContainer(
              radius: 18,
              padding: const EdgeInsets.all(16),
              blurSigma: 8,
              backgroundOpacity: 0.3,
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      ClasrumIcon(ClasrumIconKind.info,
                          size: 18, color: AppColors.blue),
                      SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Payment integration not yet available',
                          style: TextStyle(
                            color: AppColors.white,
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 8),
                  Text(
                    'This build does not include a subscription or payment flow, and no verified plan or pricing information is configured. Purchases cannot be completed or simulated from this screen.',
                    style: TextStyle(
                      color: AppColors.muted,
                      fontSize: 13,
                      height: 1.5,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4),
              child: Text(
                'Your current account status is unchanged.',
                style: TextStyle(color: AppColors.muted, fontSize: 12),
              ),
            ),
          ],
        ),
      ),
    );
  }
}