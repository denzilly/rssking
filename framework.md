# Personal News Aggregator — Project Brief

## Goal

Build a personal news dashboard / RSS aggregator with a clean, dense UI (newspaper-style, not algorithmic). The purpose is to curate an information diet across a variety of sources (NYT, FT, BBC, Hacker News, personal blogs, etc.) without being overwhelmed by firehose-volume feeds.

---

## Architecture

```
Supabase (Postgres)
  ├── feeds table        ← source config (name, url, category, max_items, priority)
  └── items table        ← fetched & scored articles

GitHub Actions (cron, every 30 min)
  └── fetcher.py         ← reads feeds from Supabase, fetches RSS, scores/deduplicates, writes items back to Supabase

Frontend (Netlify or similar static host)
  └── Single-page app    ← reads items + feeds from Supabase, UI state (read/bookmarks) in localStorage
```

---

## Key Design Decisions

### Hosting
- Frontend: Netlify (or GitHub Pages) — static site, auto-deploys on push
- Backend/cron: GitHub Actions (free, runs on schedule)
- Database: Supabase free tier (Postgres) — stores both feed config and fetched items

### Why Supabase for config (not feeds.yaml)
- Feed sources need to be editable from the frontend UI without committing to the repo
- Keeps the door open for cross-device state (read/unread, bookmarks) in the future
- Single dependency that handles both config and data storage

### Tech Stack
- Fetcher: Python (`feedparser`, `supabase-py`)
- Frontend: Vanilla HTML/JS (no build step preferred) or lightweight framework — TBD
- Styling: Clean, dense, newspaper-like — lots of signal per scroll

---

## UI Design Approach

### Workflow
1. **Initial design generation** — Use Google Stitch (https://stitch.withgoogle.com/) to generate UI variants
   - Prompt: "Dense newspaper-style RSS feed reader with category tabs"
   - Reference: Upload screenshots of FT.com, Hacker News, or similar dense layouts
   - Output: HTML/CSS/JS starting point

2. **Integration** — Copy generated code and integrate with:
   - Supabase data fetching (replace static content)
   - localStorage for read/unread tracking
   - Category filtering logic
   - Feed management UI

3. **Iteration** — Use Chrome DevTools MCP for live refinement:
   - Tweak spacing, typography, colors
   - Test responsive behavior
   - Optimize for density and scannability

4. **Deployment** — Push to Netlify/GitHub Pages

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
| url | text | unique |
| summary | text | |
| published_at | timestamp | |
| score | float | computed by fetcher |
| category | text | denormalized from feed |
| source_name | text | denormalized from feed |
| fetched_at | timestamp | |

---

## Frontend Features

- **Time window filter** — buttons for 24h / 1 week / 1 month views
- **Read/unread tracking** — via localStorage (per article URL)
- **Bookmarks / save for later** — via localStorage
- **Category filtering** — tab or sidebar filter for Finance, Tech, World News, Blogs
- **Search across headlines** — client-side, across loaded items
- **Add/edit/toggle feeds** — form in UI that writes directly to Supabase `feeds` table
- **Dense layout** — grouped by category or chronological, scannable

---

## Adding New Sources

1. User fills in "Add feed" form in the UI (name, RSS URL, category, max_items, tier)
2. Frontend writes directly to Supabase `feeds` table
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

## GitHub Actions Workflow

- Schedule: Variable frequency by time of day (all times in UTC):
  - Every 15 min during active hours (8am-8pm Mon-Fri): `cron: '*/15 8-20 * * 1-5'`
  - Every 30 min during off-hours (weekdays): `cron: '*/30 21-23,0-7 * * 1-5'`
  - Every hour on weekends: `cron: '0 * * * 0,6'`
- Steps: checkout → install deps → run `fetcher.py` → done (no commit needed, writes to Supabase)
- Secrets needed: `SUPABASE_URL`, `SUPABASE_KEY`

---

## Future Expansion Ideas (not v1)

- Cross-device sync by moving read/bookmarks from localStorage to Supabase (requires auth)
- Keyword scoring / interest tuning via a settings UI
- Full-text search via Supabase's built-in FTS
- Daily digest email via a second GitHub Actions job
- Mobile-friendly PWA wrapper
