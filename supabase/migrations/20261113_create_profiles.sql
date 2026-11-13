-- Clasrum: public.profiles for authenticated user identity (Supabase source of truth)
-- Apply with Supabase CLI (`supabase db push` / migration workflow) or in the
-- Supabase SQL editor for the target project.

begin;

-- Profiles table: one row per authenticated user.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default 'Student',
  avatar_url text null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

comment on table public.profiles is 'Per-user profile data. Identity/credentials live in auth.users.';
comment on column public.profiles.id is 'Matches auth.users.id (and auth.uid()).';

-- Keep updated_at current on row updates.
create or replace function public.set_profiles_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
before update on public.profiles
for each row
execute function public.set_profiles_updated_at();

-- Auto-create a profile when a new auth user is created.
-- Reads display_name from raw user metadata when provided (e.g. during sign up).
create or replace function public.handle_new_auth_user_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name, avatar_url, created_at, updated_at)
  values (
    new.id,
    coalesce(
      nullif(trim(new.raw_user_meta_data ->> 'display_name'), ''),
      'Student'
    ),
    nullif(trim(new.raw_user_meta_data ->> 'avatar_url'), ''),
    timezone('utc', now()),
    timezone('utc', now())
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_profile on auth.users;
create trigger on_auth_user_created_profile
after insert on auth.users
for each row
execute function public.handle_new_auth_user_profile();

-- Row Level Security: users may only access their own profile.
alter table public.profiles enable row level security;
alter table public.profiles force row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own"
on public.profiles
for select
to authenticated
using (id = auth.uid());

drop policy if exists "profiles_insert_own" on public.profiles;
create policy "profiles_insert_own"
on public.profiles
for insert
to authenticated
with check (id = auth.uid());

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own"
on public.profiles
for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

drop policy if exists "profiles_delete_own" on public.profiles;
create policy "profiles_delete_own"
on public.profiles
for delete
to authenticated
using (id = auth.uid());

-- Grants: allow authenticated clients to interact subject to RLS.
grant usage on schema public to authenticated;
grant select, insert, update, delete on table public.profiles to authenticated;

commit;
