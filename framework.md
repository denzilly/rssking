# Personal News Aggregator — Project Brief

## Goal

Build a personal news dashboard / RSS aggregator with a clean, dense UI (newspaper-style, not algorithmic). The purpose is to curate an information diet across a variety of sources (NYT, FT, BBC, Hacker News, personal blogs, etc.) without being overwhelmed by firehose-volume feeds.

Designed from day one to support multiple users (e.g. a second user browsing academic RSS feeds), each with fully independent feed subscriptions and personal state.

---

## Architecture

```
Supabase (Postgres + Auth)
  ├── feeds table         ← per-user source config (name, url, category, max_items, priority)
  ├── items table         ← shared pool of fetched & scored articles (deduped across users)
  ├── user_state table    ← per-user read/starred state (replaces localStorage)
  ├── user_profiles table ← per-user interests, display name, Claude digest prefs
  └── digests table       ← per-user AI-generated daily digest (overview + picks)

GitHub Actions (cron, every 30 min)
  ├── fetcher.py          ← loops all active feeds across all users, fetches RSS,
  │                          scores/deduplicates, writes items to shared pool
  └── digest.py           ← runs 1-2x/day, calls Claude API per user, writes digest to Supabase

Frontend (Netlify — static site, auto-deploys on push)
  └── Single-page app     ← Supabase Auth login, reads user's feeds + items,
                             state stored in Supabase (not localStorage)
```

---

## Key Design Decisions

### Hosting
- Frontend: Netlify — static site, auto-deploys on push
- Backend/cron: GitHub Actions (free, runs on schedule)
- Database + Auth: Supabase free tier (Postgres + built-in Auth + RLS)

### Why Supabase for config (not feeds.yaml)
- Feed sources need to be editable from the frontend UI without committing to the repo
- Single dependency handles config, data storage, auth, and row-level security
- RLS policies mean per-user data isolation is enforced at the database level

### Multi-user model: per-user feeds, shared item pool
- Each user owns their own rows in the `feeds` table — fully independent subscriptions
- Items are fetched into a shared pool and deduplicated across users (efficient storage)
- All personal state (read, starred, digest, interests) is per-user in Supabase
- Users can have completely different categories and feed sets (e.g. news vs. academic journals)

### Tech Stack
- Fetcher: Python (`feedparser`, `supabase-py`, `anthropic`)
- Frontend: Vanilla HTML/JS (no build step preferred) or lightweight framework — TBD
- Styling: Clean, dense, newspaper-like — lots of signal per scroll

---

## Security Model

### How Supabase Auth + RLS works
- **`anon` key** — intentionally public, safe to ship in frontend JS. Supabase designed it this way. Security comes from RLS policies, not key secrecy.
- **`service_role` key** — full admin access, bypasses RLS. Used only in GitHub Actions secrets. Never in frontend code.
- **Row Level Security (RLS)** — enforced at the Postgres level, not the app level. Even if someone bypasses the frontend, the database itself rejects unauthorized queries. Users can only ever see their own feeds, state, and digests.

### Configuration checklist (do before going live)
- [ ] Disable public registration in Supabase dashboard → invite users manually
- [ ] Enable RLS on all tables: `feeds`, `user_state`, `user_profiles`, `digests`
- [ ] Add RLS policy on each table: `user_id = auth.uid()`
- [ ] `items` table: readable by any authenticated user (shared pool), writable only by service role (fetcher)
- [ ] `service_role` key only in GitHub Actions secrets — never committed to repo
- [ ] Netlify serves over HTTPS automatically — no config needed

### What this protects against
- One user reading another user's feeds, state, or digest — blocked by RLS
- Unauthenticated access to any data — blocked by RLS (anon role gets nothing)
- Leaked `anon` key being abused — limited to what RLS allows (nothing without valid login)
- Leaked `service_role` key — the only real risk; keep it in GitHub Secrets only

---

## UI Design Approach

### Workflow
1. **Initial design generation** — Use Google Stitch (https://stitch.withgoogle.com/) to generate UI variants
   - Prompt: "Dense newspaper-style RSS feed reader with category tabs"
   - Reference: Upload screenshots of FT.com, Hacker News, or similar dense layouts
   - Output: HTML/CSS/JS starting point — saved in `stitch/` folder

2. **Integration** — Copy generated code and integrate with:
   - Supabase Auth (login screen, session handling)
   - Supabase data fetching (replace static content)
   - Per-user state (read/starred stored in Supabase, not localStorage)
   - Category filtering logic
   - Feed management UI

3. **Iteration** — Use Chrome DevTools MCP for live refinement:
   - Tweak spacing, typography, colors
   - Test responsive behavior
   - Optimize for density and scannability

4. **Deployment** — Push to Netlify

### Design Goals
- **Dense, newspaper-style layout** — maximize information per scroll
- **Clean visual hierarchy** — scannable headlines, clear source badges
- **Minimal styling** — fast load times, no heavy frameworks
- **Mobile-friendly** — responsive but density-first

---

## Information Overload Strategy

The firehose problem (e.g. FT publishing 100 articles/day) is solved by:

1. **Per-source caps** — configurable `max_items` per feed (e.g. FT: 5, HN: 10)

2. **Multi-signal scoring** — items ranked by:
   - **Source tier** (feed-level):
     - Tier 1 (Curated): Editorial picks, breaking news feeds → +40 points
     - Tier 2 (Standard): Everything else (news, blogs, HN) → +20 points
   - **Time decay**: Recent items boosted (0-50 points based on age)
   - **Multi-source correlation**: Same story in 3+ feeds → +40 points
   - **Feed metadata**: RSS tags for "featured" or "breaking" → +30 points
   - **Title patterns**: "BREAKING:", "URGENT:" etc. → +20 points
   - **Keyword matching**: User interests (+20) and noise filtering (-30)

   Formula: `score = tier_weight + time_decay + multi_source + metadata + title_patterns + keywords`

3. **Time window filtering** (user-controlled in UI):
   - **24 hours**: Breaking news, daily check-in
   - **1 week**: Catching up after a few days
   - **1 month**: Deep catch-up, infrequent blogs
   - Backend fetches items from last 30 days, frontend filters by selected window

4. **Deduplication** — same story from multiple sources collapsed into one item with multiple source badges

5. **Category filtering** — feeds tagged by category (Finance, Tech, World News, Blogs) so user can focus

6. **Database cleanup** — periodically delete items older than 30-60 days

---

## Supabase Schema

### `feeds` table
| column | type | notes |
|---|---|---|
| id | uuid | primary key |
| user_id | uuid | foreign key → auth.users (RLS: owner only) |
| name | text | e.g. "Financial Times" |
| url | text | RSS feed URL |
| category | text | e.g. "Finance", "Tech" |
| max_items | int | cap per fetch cycle |
| tier | int | 1 = curated/editorial, 2 = standard |
| active | bool | toggle without deleting |
| created_at | timestamp | |

### `items` table
| column | type | notes |
|---|---|---|
| id | uuid | primary key |
| feed_id | uuid | foreign key → feeds |
| title | text | |
| url | text | unique — deduplication key |
| summary | text | |
| published_at | timestamp | |
| score | float | computed by fetcher |
| category | text | denormalized from feed |
| source_name | text | denormalized from feed |
| fetched_at | timestamp | |

RLS: readable by any authenticated user. Writable only by service role (fetcher).

### `user_state` table
| column | type | notes |
|---|---|---|
| id | uuid | primary key |
| user_id | uuid | foreign key → auth.users (RLS: owner only) |
| item_id | uuid | foreign key → items |
| read | bool | default false |
| starred | bool | default false |
| updated_at | timestamp | |

### `user_profiles` table
| column | type | notes |
|---|---|---|
| user_id | uuid | primary key, foreign key → auth.users |
| display_name | text | |
| interests | text | free-form text sent to Claude as interest profile |
| updated_at | timestamp | |

### `digests` table
| column | type | notes |
|---|---|---|
| id | uuid | primary key |
| user_id | uuid | foreign key → auth.users (RLS: owner only) |
| generated_at | timestamp | |
| overview | text | 2-3 sentence news summary paragraph |
| picks | jsonb | array of `{item_id, reason}` — Claude's personalized picks |
| time_window_hours | int | 24 or 48 |

---

## Claude AI Digest

A second GitHub Actions job (`digest.py`) runs 1-2x per day and generates a personalized digest per user.

### How it works
1. For each active user, query their subscribed feeds' items from the last 24h
2. Pull the user's `interests` text from `user_profiles`
3. Pull their recent starred items (implicit interest signal)
4. Send titles + summaries to Claude API with a system prompt:
   ```
   You are a personal news curator. The user's interests: [interests text].
   Articles they've starred recently: [last 20 starred titles].
   Given these N articles from the last 24h, write:
   1. A 2-3 sentence overview of the most important news
   2. 5 picks you think this user would find interesting, with a one-line reason each
   ```
5. Store result in `digests` table

### What Claude sees
- Article titles and RSS excerpt/summary (not full article text — paywalled articles provide only a lede)
- This is sufficient for curation and overview purposes
- Cost: ~50-100K tokens/month per user — well under $1/month

### Frontend display
- "Digest" panel at top of feed — overview paragraph + Claude's picks with a badge
- "My Interests" field in settings — editable text sent to Claude

---

## Frontend Features

- **Login screen** — Supabase Auth (email/password or magic link)
- **Time window filter** — buttons for 24h / 1 week / 1 month views
- **Read/unread tracking** — stored in Supabase `user_state` (cross-device, per-user)
- **Starred / save for later** — stored in Supabase `user_state`
- **Category filtering** — tab or sidebar filter, per user's own categories
- **Search across headlines** — client-side, across loaded items
- **Add/edit/toggle feeds** — form in UI that writes to user's rows in `feeds` table
- **Dense layout** — grouped by category or chronological, scannable
- **Daily digest panel** — Claude overview + personalized picks

---

## Adding New Sources

1. User fills in "Add feed" form in the UI (name, RSS URL, category, max_items, tier)
2. Frontend writes to `feeds` table with `user_id = auth.uid()` (RLS enforced)
3. Next GitHub Actions run picks it up automatically — no code commit needed

### Finding RSS URLs
- Try appending `/feed`, `/rss`, `/feed.xml` to any domain
- Use browser extension: **RSS Finder** or **Feedbro**
- For non-RSS sources: **RSSHub** (`rsshub.app`) generates feeds for YouTube channels, Reddit, GitHub releases, etc.
- For newsletters: **Kill the Newsletter** converts email newsletters to RSS

---

## Suggested Initial Sources (to seed `feeds` table)

| Name | Category | Notes |
|---|---|---|
| Financial Times | Finance | Limited RSS without subscription |
| NYT | World News | |
| BBC News | World News | |
| Hacker News | Tech | Use HN JSON API or RSS |
| Ars Technica | Tech | |
| Personal blogs | Blogs | User to add |

---

## GitHub Actions Workflows

### `fetcher.py` (RSS fetch — runs every 15-60 min)
- Schedule: Variable frequency by time of day (all times in UTC):
  - Every 15 min during active hours (8am-8pm Mon-Fri): `cron: '*/15 8-20 * * 1-5'`
  - Every 30 min during off-hours (weekdays): `cron: '*/30 21-23,0-7 * * 1-5'`
  - Every hour on weekends: `cron: '0 * * * 0,6'`
- Loops all active feeds across all users (union of all `feeds` rows where `active = true`)
- Deduplicates items by URL across users before writing
- Secrets needed: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service role — bypasses RLS)

### `digest.py` (Claude digest — runs 1-2x/day)
- Schedule: e.g. `cron: '0 7,17 * * *'` (7am + 5pm UTC)
- For each user: fetches their items, calls Claude API, writes to `digests` table
- Secrets needed: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`

---

## Future Expansion Ideas (not v1)

- Full-text search via Supabase's built-in FTS
- Mobile-friendly PWA wrapper
- Keyword scoring / interest tuning via a settings UI (currently free-text interests field)
- Subscriber RSS feeds for FT/NYT (fuller excerpts for paying subscribers)
- Email digest delivery via a third GitHub Actions job
