-- =============================================================
-- RSSKING Schema
-- Run this in the Supabase SQL editor (once, on a fresh project)
-- =============================================================


-- =============================================================
-- 1. FEEDS
-- Each user owns their own feed subscriptions.
-- =============================================================
create table public.feeds (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,
  url         text not null,
  category    text not null default 'Uncategorized',
  max_items   int  not null default 10,
  tier        int  not null default 2,  -- 1 = curated/editorial, 2 = standard
  active      bool not null default true,
  created_at  timestamptz not null default now()
);

alter table public.feeds enable row level security;

create policy "Users can manage their own feeds"
  on public.feeds
  for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);


-- =============================================================
-- 2. ITEMS
-- Shared pool of fetched articles. Deduplicated by URL.
-- Written only by the fetcher (service role). Readable by all
-- authenticated users.
-- =============================================================
create table public.items (
  id            uuid primary key default gen_random_uuid(),
  feed_id       uuid not null references public.feeds(id) on delete cascade,
  title         text not null,
  url           text not null unique,
  summary       text,
  published_at  timestamptz,
  score         float not null default 0,
  category      text,
  source_name   text,
  fetched_at    timestamptz not null default now()
);

alter table public.items enable row level security;

-- Any authenticated user can read items
create policy "Authenticated users can read items"
  on public.items
  for select
  using (auth.role() = 'authenticated');

-- Only service role (fetcher) can insert/update/delete
-- (no insert policy = only service_role key can write)

create index items_feed_id_idx        on public.items(feed_id);
create index items_published_at_idx   on public.items(published_at desc);
create index items_score_idx          on public.items(score desc);
create index items_url_idx            on public.items(url);


-- =============================================================
-- 3. USER STATE
-- Per-user read/starred state for each article.
-- Replaces localStorage — syncs across devices.
-- =============================================================
create table public.user_state (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  item_id     uuid not null references public.items(id) on delete cascade,
  read        bool not null default false,
  starred     bool not null default false,
  updated_at  timestamptz not null default now(),
  unique(user_id, item_id)
);

alter table public.user_state enable row level security;

create policy "Users can manage their own state"
  on public.user_state
  for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index user_state_user_id_idx on public.user_state(user_id);
create index user_state_item_id_idx on public.user_state(item_id);


-- =============================================================
-- 4. USER PROFILES
-- One row per user. Stores display name and Claude interest
-- profile text used to personalise the daily digest.
-- Created automatically on first login via trigger below.
-- =============================================================
create table public.user_profiles (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  display_name  text,
  interests     text,  -- free-form text sent to Claude as interest profile
  updated_at    timestamptz not null default now()
);

alter table public.user_profiles enable row level security;

create policy "Users can manage their own profile"
  on public.user_profiles
  for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Auto-create a profile row when a new user signs up
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.user_profiles (user_id, display_name)
  values (new.id, new.email);
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- =============================================================
-- 5. DIGESTS
-- Per-user AI-generated daily digests from Claude.
-- Written by digest.py (service role). Read by the user.
-- =============================================================
create table public.digests (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references auth.users(id) on delete cascade,
  generated_at       timestamptz not null default now(),
  overview           text,        -- 2-3 sentence news summary
  picks              jsonb,       -- [{item_id, reason}, ...]
  time_window_hours  int not null default 24
);

alter table public.digests enable row level security;

create policy "Users can read their own digests"
  on public.digests
  for select
  using (auth.uid() = user_id);

create index digests_user_id_generated_at_idx
  on public.digests(user_id, generated_at desc);
