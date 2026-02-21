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

## Information Overload Strategy

The firehose problem (e.g. FT publishing 100 articles/day) is solved by:

1. **Per-source caps** — configurable `max_items` per feed (e.g. FT: 5, HN: 10)
2. **Scoring/ranking** — items scored by recency + source priority + keyword interest lists
3. **Deduplication** — same story from multiple sources collapsed into one item with multiple source badges
4. **Category filtering** — feeds tagged by category (Finance, Tech, World News, Blogs) so user can focus

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
| priority | text | "high" / "medium" / "low" |
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

- **Read/unread tracking** — via localStorage (per article URL)
- **Bookmarks / save for later** — via localStorage
- **Source filtering by category** — tab or sidebar filter
- **Search across headlines** — client-side, across loaded items
- **Add/edit/toggle feeds** — form in UI that writes directly to Supabase `feeds` table
- **Dense layout** — grouped by category or chronological, scannable

---

## Adding New Sources

1. User fills in "Add feed" form in the UI (name, RSS URL, category, max_items, priority)
2. Frontend writes directly to Supabase `feeds` table
3. Next GitHub Actions run (within 30 min) picks it up automatically — no code commit needed

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

- Schedule: every 30 minutes (`cron: '*/30 * * * *'`)
- Steps: checkout → install deps → run `fetcher.py` → done (no commit needed, writes to Supabase)
- Secrets needed: `SUPABASE_URL`, `SUPABASE_KEY`

---

## Future Expansion Ideas (not v1)

- Cross-device sync by moving read/bookmarks from localStorage to Supabase (requires auth)
- Keyword scoring / interest tuning via a settings UI
- Full-text search via Supabase's built-in FTS
- Daily digest email via a second GitHub Actions job
- Mobile-friendly PWA wrapper
