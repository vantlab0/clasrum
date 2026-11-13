# Supabase Authentication Foundation (Phase 7)

This document describes the Supabase auth/profile foundation added to Clasrum (Flutter + existing FastAPI/Qwen backend). **Authentication screens are not implemented in this phase.**

## 1. Flutter configuration (build-time, no secrets committed)

The app reads Supabase configuration via `--dart-define` in `app/lib/core/config.dart`:

- `SUPABASE_URL` — Supabase project URL (e.g. `https://<project-ref>.supabase.co`)
- `SUPABASE_ANON_KEY` or `SUPABASE_PUBLISHABLE_KEY` — Supabase publishable/anon key (**never** service-role key)
- `API_BASE_URL` — existing backend origin (unchanged)

Example:

```sh
cd app
flutter run -d chrome \
  --dart-define=API_BASE_URL=http://127.0.0.1:8000 \
  --dart-define=SUPABASE_URL=https://<project-ref>.supabase.co \
  --dart-define=SUPABASE_ANON_KEY=<publishable-or-anon-key>
```

`AppConfig.supabaseConfigError` reports missing values. `SupabaseBootstrap.initialize(...)` records initialization/config errors and `main.dart` shows a clear configuration error screen if Supabase cannot be initialized (graceful failure; no credentials are hardcoded/committed).

## 2. Supabase initialization

- Package: `supabase_flutter` (`app/pubspec.yaml`)
- Bootstrap: `app/lib/core/supabase_bootstrap.dart`
- Startup: `app/lib/main.dart` calls `WidgetsFlutterBinding.ensureInitialized()`, loads config/storage, then `SupabaseBootstrap.initialize(config)` before `runApp`.
- Services exposed via `AuthScope` (InheritedWidget, consistent with existing architecture): `AuthService`, `ProfileService`.

## 3. Authentication service (`app/lib/services/auth_service.dart`)

Centralized `AuthService` (ChangeNotifier) provides:

- `signUpWithPassword(email, password, displayName?, emailRedirectTo?)`
- `signInWithPassword(email, password)`
- `signOut()`
- `resetPassword(email, redirectTo?)` (email reset flow)
- `resendSignupVerificationEmail(email, emailRedirectTo?)`
- `verifyOtp(email, token, type)` (exchange email verification/recovery/magic-link OTP codes when received)
- `currentUser`, `currentSession`, `isSignedIn`, `accessToken`
- `authStateChanges` stream and listener-based state updates
- `restoreSession()` / session sync on startup and auth-state changes

Session persistence/restoration is handled by `supabase_flutter` internally; `AuthService` also mirrors the active Supabase access token into `AppStorage.token` so existing backend/WS callers that read the stored bearer token can forward Supabase tokens when available.

## 4. Profile service (`app/lib/services/profile_service.dart`)

Centralized `ProfileService` uses Supabase as the source of truth for authenticated identity and `public.profiles` rows:

- `getCurrentProfile()` — reads own profile (`eq('id', auth.uid())` via current session)
- `upsertCurrentProfile(...)`, `updateCurrentProfile(...)` — updates own profile fields
- `ensureCurrentProfile(...)` — creates/fetches own profile row (client-side fallback)

`profiles` stores only profile data (`id`, `display_name`, `avatar_url`, timestamps). **No Supabase Auth credentials are duplicated.** Authorization relies on authenticated session + RLS (and never trusts a client-supplied user ID for authorization).

## 5. Database migration (Supabase/Postgres)

Migration added: `supabase/migrations/20261113_create_profiles.sql`

**What it does:**

1. Creates `public.profiles` if missing:
   - `id uuid primary key references auth.users(id) on delete cascade`
   - `display_name text not null default 'Student'`
   - `avatar_url text null`
   - `created_at timestamptz not null default now() (UTC)`
   - `updated_at timestamptz not null default now() (UTC)`
2. Adds `set_profiles_updated_at()` trigger to maintain `updated_at` on updates.
3. Adds `handle_new_auth_user_profile()` (`security definer`) trigger on `auth.users` **after insert** to auto-create a profile row (uses `raw_user_meta_data.display_name` when present; `on conflict do nothing`).
4. Enables and forces RLS on `public.profiles`.
5. Creates per-user RLS policies (authenticated role) using `auth.uid()`:
   - `profiles_select_own` (SELECT) — `using (id = auth.uid())`
   - `profiles_insert_own` (INSERT) — `with check (id = auth.uid())`
   - `profiles_update_own` (UPDATE) — `using (id = auth.uid()) with check (id = auth.uid())`
   - `profiles_delete_own` (DELETE) — `using (id = auth.uid())`
6. Grants `select/insert/update/delete` on `public.profiles` to `authenticated` (RLS still enforced).

**Apply:** run through Supabase CLI/migration workflow (e.g. `supabase db push` / `supabase migration up` depending on project setup) or execute in the Supabase SQL editor for the correct project/environment. Review in staging before production.

## 6. Supabase dashboard configuration required

Configure in the Supabase project dashboard (values depend on your app/deep-link setup):

- **Authentication > Providers > Email**: enable Email provider.
- **Email confirmations / verification**: enable "Confirm email" if you require verified email before full access; ensure signup confirmation emails are configured. (App exposes signup and resend/verify helpers; UI flow is out of scope for this phase.)
- **Password reset**: ensure reset email templates are enabled; set a valid **Redirect URL** / Site URL so reset links return to your app (web URL or mobile deep link) and can be handled by app routing/deep-link code when implemented.
- **URL Configuration**: set Site URL and allowed Redirect URLs for your environments (localhost/dev, production web, mobile deep links as applicable).
- **API keys**: use the project URL and **publishable/anon** key in the Flutter app. **Do not use the service-role key in Flutter.**
- **Auth settings**: review session/JWT settings as needed for your environment. If backend verification of Supabase JWTs is enabled (see below), ensure backend has the correct Supabase JWT secret / verification configuration for the same project.
- **Social providers (Google/Apple)**: only configure/enable when provider credentials and Supabase setup are actually available; do not expose non-functional buttons (no auth UI added in this phase).

## 7. Backend compatibility (existing FastAPI + realtime Qwen)

- Existing backend continues to identify users via bearer JWT -> local `users` record (`resolve_student` / `decode_access_token`).
- Flutter now forwards the active Supabase access token into stored bearer token when signed in via Supabase, so existing REST/WS callers send it automatically.
- Backend token verification was extended to optionally accept Supabase JWTs when configured (`SUPABASE_JWT_SECRET`/related settings in `app/config.py` and `app/core/supabase_auth.py`/security integration as implemented), resolving/mapping to a local backend user record without trusting client-supplied user IDs. This is best-effort compatibility plumbing and must be configured to match the Supabase project issuing tokens.
- No unrelated API or realtime contract rewrites were made.

## 8. Security notes

- Supabase service-role key is never included in Flutter.
- `public.profiles` has RLS enabled+forced with `auth.uid()` policies; clients can only access their own profile.
- Profile operations in `ProfileService` are scoped to the current authenticated user; the app does not use client-supplied user IDs for authorization.
- Backend ownership checks remain server-side (`lesson.student_id` etc.).

## 9. Tests / validation

- `flutter pub get` required after dependency changes.
- `flutter analyze lib` and existing Flutter tests should be run from `app/` after pulling changes in an environment with network access to fetch packages.
- Backend compatibility changes should be validated with backend test suite and configured Supabase secrets in non-production environments.
