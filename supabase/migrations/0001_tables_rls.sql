-- Pulse-HWM cloud schema + Row Level Security
--
-- Applied by hand in the Supabase dashboard (SQL editor) or via the CLI.
-- Everything a logged-in device needs is here:
--   * profiles      — 1:1 mirror of auth.users (email identity only)
--   * user_settings — one row per syncable setting key (per-key LWW)
--   * user_sites    — monitored sites, soft-deleted, per-row LWW
--
-- SECURITY MODEL: the desktop app holds only the publishable/anon key.
-- RLS is the fence — each user can touch ONLY their own rows. The
-- service_role key never leaves the Supabase dashboard.

-- ── profiles ──────────────────────────────────────────────────────────
create table if not exists public.profiles (
    id         uuid primary key references auth.users (id) on delete cascade,
    email      text not null,
    created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "own profile: select" on public.profiles
    for select using (auth.uid() = id);
create policy "own profile: insert" on public.profiles
    for insert with check (auth.uid() = id);
create policy "own profile: update" on public.profiles
    for update using (auth.uid() = id);

-- ── user_settings ─────────────────────────────────────────────────────
create table if not exists public.user_settings (
    user_id    uuid not null references auth.users (id) on delete cascade,
    key        text not null,
    value      text not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, key)
);

alter table public.user_settings enable row level security;

create policy "own settings: select" on public.user_settings
    for select using (auth.uid() = user_id);
create policy "own settings: insert" on public.user_settings
    for insert with check (auth.uid() = user_id);
create policy "own settings: update" on public.user_settings
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own settings: delete" on public.user_settings
    for delete using (auth.uid() = user_id);

-- ── user_sites ────────────────────────────────────────────────────────
create table if not exists public.user_sites (
    user_id         uuid not null references auth.users (id) on delete cascade,
    site_uuid       uuid not null,
    name            text not null,
    url             text not null,
    method          text not null default 'GET',
    timeout_s       real not null default 10.0,
    expected_status integer not null default 200,
    keyword         text not null default '',
    enabled         boolean not null default true,
    deleted         boolean not null default false,
    updated_at      timestamptz not null default now(),
    primary key (user_id, site_uuid)
);

alter table public.user_sites enable row level security;

create policy "own sites: select" on public.user_sites
    for select using (auth.uid() = user_id);
create policy "own sites: insert" on public.user_sites
    for insert with check (auth.uid() = user_id);
create policy "own sites: update" on public.user_sites
    for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own sites: delete" on public.user_sites
    for delete using (auth.uid() = user_id);
