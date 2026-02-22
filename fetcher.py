"""
fetcher.py — RSS feed fetcher for RSSKING
Runs via GitHub Actions on a schedule.
Reads all active feeds from Supabase, fetches RSS, scores and
deduplicates articles, then writes new items back to Supabase.

Required environment variables:
  SUPABASE_URL          — your project URL (https://xxx.supabase.co)
  SUPABASE_SERVICE_KEY  — service_role key (bypasses RLS, server-side only)
"""

import os
import re
import hashlib
import logging
from datetime import datetime, timezone, timedelta

import feedparser
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Items older than this are ignored
MAX_AGE_DAYS = 30

# ------------------------------------------------------------------
# Scoring constants
# ------------------------------------------------------------------
TIER_WEIGHTS      = {1: 40, 2: 20}
TIME_DECAY_MAX    = 50
MULTI_SOURCE_BUMP = 40
METADATA_BUMP     = 30
TITLE_PATTERN_BUMP = 20
KEYWORD_BOOST     = 20
KEYWORD_PENALTY   = -30

BREAKING_PATTERNS = re.compile(
    r"\b(breaking|urgent|flash|alert|exclusive)\b", re.IGNORECASE
)

NOISE_KEYWORDS = [
    "sponsored", "advertisement", "buy now", "subscribe now",
    "limited offer", "click here",
]


def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all_active_feeds(sb: Client) -> list[dict]:
    """Return all active feed rows across all users."""
    res = sb.table("feeds").select("*").eq("active", True).execute()
    return res.data or []


def fetch_existing_urls(sb: Client) -> set[str]:
    """Return URLs already in the items table (for deduplication)."""
    res = sb.table("items").select("url").execute()
    return {row["url"] for row in (res.data or [])}


def score_item(
    item: dict,
    feed: dict,
    url_feed_count: dict[str, int],
) -> float:
    """Compute a relevance score for an RSS item."""
    score = 0.0

    # Tier weight
    score += TIER_WEIGHTS.get(feed.get("tier", 2), 20)

    # Time decay (0–50 points, linear over MAX_AGE_DAYS)
    published = item.get("_published_dt")
    if published:
        age_hours = (datetime.now(timezone.utc) - published).total_seconds() / 3600
        age_days  = age_hours / 24
        decay     = max(0, 1 - age_days / MAX_AGE_DAYS)
        score    += decay * TIME_DECAY_MAX

    # Multi-source correlation
    url = item.get("link", "")
    if url_feed_count.get(url, 1) >= 3:
        score += MULTI_SOURCE_BUMP

    # RSS metadata tags
    tags = [t.get("term", "").lower() for t in item.get("tags", [])]
    if any(t in ("featured", "breaking", "top-news", "editors-pick") for t in tags):
        score += METADATA_BUMP

    # Title patterns
    title = item.get("title", "")
    if BREAKING_PATTERNS.search(title):
        score += TITLE_PATTERN_BUMP

    # Noise penalty
    combined = (title + " " + item.get("summary", "")).lower()
    if any(kw in combined for kw in NOISE_KEYWORDS):
        score += KEYWORD_PENALTY

    return round(score, 2)


def parse_published(entry: dict):
    """Return a timezone-aware datetime from a feedparser entry, or None."""
    if entry.get("published_parsed"):
        try:
            t = entry.published_parsed
            return datetime(*t[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def summarise(entry: dict, max_chars: int = 500) -> str:
    """Extract a short plain-text summary from a feedparser entry."""
    text = ""
    if entry.get("summary"):
        text = entry.summary
    elif entry.get("content"):
        text = entry.content[0].get("value", "")

    # Strip basic HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def main():
    sb = get_supabase()

    log.info("Fetching active feeds from Supabase…")
    feeds = fetch_all_active_feeds(sb)
    log.info(f"Found {len(feeds)} active feed(s)")

    if not feeds:
        log.info("Nothing to do.")
        return

    existing_urls = fetch_existing_urls(sb)
    log.info(f"{len(existing_urls)} items already in database")

    # First pass: collect all candidate entries so we can compute
    # multi-source correlation (same URL appearing in multiple feeds)
    url_feed_count: dict[str, int] = {}
    all_candidates: list[tuple[dict, dict, dict]] = []  # (entry, feed, parsed_feed)

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    for feed in feeds:
        log.info(f"Fetching: {feed['name']} ({feed['url']})")
        try:
            parsed = feedparser.parse(feed["url"], request_headers={"User-Agent": "RSSKing/1.0"}, timeout=10)
        except Exception as e:
            log.warning(f"  Failed to fetch {feed['url']}: {e}")
            continue

        entries = parsed.entries[: feed.get("max_items", 10)]
        for entry in entries:
            url = entry.get("link", "").strip()
            if not url:
                continue

            published = parse_published(entry)
            entry["_published_dt"] = published

            # Skip if too old
            if published and published < cutoff:
                continue

            url_feed_count[url] = url_feed_count.get(url, 0) + 1
            all_candidates.append((entry, feed, parsed))

    # Second pass: score and collect new items
    new_items: list[dict] = []
    seen_urls: set[str] = set()

    for entry, feed, parsed in all_candidates:
        url = entry.get("link", "").strip()

        # Skip duplicates already in DB or seen in this run
        if url in existing_urls or url in seen_urls:
            continue
        seen_urls.add(url)

        published = entry.get("_published_dt")
        score     = score_item(entry, feed, url_feed_count)

        new_items.append({
            "feed_id":      feed["id"],
            "title":        (entry.get("title") or "").strip()[:500],
            "url":          url,
            "summary":      summarise(entry),
            "published_at": published.isoformat() if published else None,
            "score":        score,
            "category":     feed.get("category", "Uncategorized"),
            "source_name":  feed.get("name", ""),
        })

    log.info(f"{len(new_items)} new item(s) to insert")

    if not new_items:
        log.info("No new items. Done.")
        return

    # Batch insert in chunks of 100
    chunk_size = 100
    inserted = 0
    for i in range(0, len(new_items), chunk_size):
        chunk = new_items[i : i + chunk_size]
        try:
            sb.table("items").insert(chunk).execute()
            inserted += len(chunk)
            log.info(f"  Inserted {inserted}/{len(new_items)}")
        except Exception as e:
            log.error(f"  Insert error: {e}")

    log.info(f"Done. {inserted} new item(s) written to Supabase.")

    # Clean up items older than MAX_AGE_DAYS (also delete items with no publish date
    # older than MAX_AGE_DAYS based on fetched_at as a fallback)
    old_cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    try:
        sb.table("items").delete().lt("published_at", old_cutoff).execute()
        sb.table("items").delete().is_("published_at", "null").lt("fetched_at", old_cutoff).execute()
        log.info("Old items cleaned up.")
    except Exception as e:
        log.warning(f"Cleanup error (non-fatal): {e}")


if __name__ == "__main__":
    main()
