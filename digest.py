"""
digest.py — Daily AI digest for RSSKING
Runs via GitHub Actions once or twice per day.
For each active user, fetches their recent articles, calls Claude,
and writes a digest (overview + personalized picks) to Supabase.

Required environment variables:
  SUPABASE_URL          — your project URL
  SUPABASE_SERVICE_KEY  — service_role key (bypasses RLS)
  ANTHROPIC_API_KEY     — from console.anthropic.com
"""

import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta

import anthropic
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

CLAUDE_MODEL      = "claude-haiku-4-5-20251001"  # fast + cheap for digest work
TIME_WINDOW_HOURS = 24
MAX_PICKS         = 5
MAX_ARTICLES      = 200  # cap articles sent to Claude per user


def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_anthropic() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def fetch_users(sb: Client) -> list[dict]:
    """Return all users who have at least one active feed."""
    res = sb.table("feeds").select("user_id").eq("active", True).execute()
    user_ids = list({row["user_id"] for row in (res.data or [])})

    if not user_ids:
        return []

    # Get profiles for those users
    profiles = sb.table("user_profiles").select("*").in_("user_id", user_ids).execute()
    profile_map = {p["user_id"]: p for p in (profiles.data or [])}

    return [
        {"user_id": uid, "profile": profile_map.get(uid, {})}
        for uid in user_ids
    ]


def fetch_user_items(sb: Client, user_id: str) -> list[dict]:
    """Return recent items from this user's feeds, ordered by score."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)).isoformat()

    # Get user's feed IDs
    feeds_res = sb.table("feeds").select("id").eq("user_id", user_id).eq("active", True).execute()
    feed_ids  = [f["id"] for f in (feeds_res.data or [])]
    if not feed_ids:
        return []

    items_res = sb.table("items") \
        .select("id, title, url, summary, source_name, category, score, published_at") \
        .in_("feed_id", feed_ids) \
        .gte("published_at", cutoff) \
        .order("score", desc=True) \
        .limit(MAX_ARTICLES) \
        .execute()

    return items_res.data or []


def fetch_user_starred(sb: Client, user_id: str, limit: int = 30) -> list[dict]:
    """Return titles of items the user has recently starred (interest signal)."""
    res = sb.table("user_state") \
        .select("item_id") \
        .eq("user_id", user_id) \
        .eq("starred", True) \
        .order("updated_at", desc=True) \
        .limit(limit) \
        .execute()

    item_ids = [r["item_id"] for r in (res.data or [])]
    if not item_ids:
        return []

    items = sb.table("items").select("title").in_("id", item_ids).execute()
    return items.data or []


def sanitise_text(text: str, max_chars: int = 2000) -> str:
    """Strip control characters and limit length before embedding in prompts."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return cleaned[:max_chars]


def build_prompt(items: list[dict], profile: dict, starred: list[dict]) -> str:
    interests     = sanitise_text((profile.get("interests") or "").strip())
    display_name  = sanitise_text((profile.get("display_name") or "this user").strip(), max_chars=100)
    starred_titles = [sanitise_text(s["title"], max_chars=200) for s in starred]

    # Format article list for Claude
    articles_text = "\n".join(
        f"{i+1}. [{item['source_name']}] {item['title']}"
        + (f" — {item['summary'][:200]}" if item.get("summary") else "")
        for i, item in enumerate(items)
    )

    return f"""You are a personal news curator for {display_name}.

USER INTERESTS:
{interests if interests else "(not specified — use general news judgment)"}

ARTICLES THIS USER HAS STARRED RECENTLY (implicit interest signal):
{chr(10).join(f'- {t}' for t in starred_titles) if starred_titles else "(none yet)"}

ARTICLES FROM THE LAST {TIME_WINDOW_HOURS} HOURS ({len(items)} total):
{articles_text}

YOUR TASK:
1. Write a 2-3 sentence overview of the most important news from this list. Be specific — name events, people, or themes. Do not be generic.

2. Pick exactly {MAX_PICKS} articles from the list that you think {display_name} would find most interesting, based on their stated interests and starred history. For each pick, give a one-sentence reason.

RESPOND IN THIS EXACT JSON FORMAT (no markdown, no preamble):
{{
  "overview": "...",
  "picks": [
    {{"index": 1, "reason": "..."}},
    {{"index": 2, "reason": "..."}},
    {{"index": 3, "reason": "..."}},
    {{"index": 4, "reason": "..."}},
    {{"index": 5, "reason": "..."}}
  ]
}}

Where "index" is the 1-based article number from the list above."""


def call_claude(client: anthropic.Anthropic, prompt: str) -> dict | None:
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.error(f"Claude returned invalid JSON: {e}")
        return None
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None


def write_digest(sb: Client, user_id: str, items: list[dict], result: dict):
    """Convert Claude's response into a digest row and write to Supabase."""
    raw_picks = result.get("picks")
    if not isinstance(raw_picks, list):
        raw_picks = []

    picks = []
    for pick in raw_picks:
        if not isinstance(pick, dict):
            continue
        one_based = pick.get("index")
        # Strictly validate: must be an integer in range [1, len(items)]
        if not isinstance(one_based, int) or not (1 <= one_based <= len(items)):
            log.warning(f"  Skipping pick with invalid index: {one_based}")
            continue
        picks.append({
            "item_id": items[one_based - 1]["id"],
            "reason":  str(pick.get("reason", ""))[:500],
        })

    sb.table("digests").insert({
        "user_id":            user_id,
        "overview":           result.get("overview", ""),
        "picks":              picks,
        "time_window_hours":  TIME_WINDOW_HOURS,
        "generated_at":       datetime.now(timezone.utc).isoformat(),
    }).execute()


def main():
    sb     = get_supabase()
    claude = get_anthropic()

    users = fetch_users(sb)
    log.info(f"Generating digest for {len(users)} user(s)")

    for user in users:
        user_id = user["user_id"]
        profile = user["profile"]
        name    = profile.get("display_name") or user_id[:8]
        log.info(f"Processing: {name}")

        items = fetch_user_items(sb, user_id)
        if not items:
            log.info(f"  No recent items for {name}, skipping")
            continue

        log.info(f"  {len(items)} articles to summarise")
        starred = fetch_user_starred(sb, user_id)
        log.info(f"  {len(starred)} starred articles as interest signal")

        prompt = build_prompt(items, profile, starred)
        result = call_claude(claude, prompt)

        if not result:
            log.warning(f"  Failed to get digest for {name}")
            continue

        write_digest(sb, user_id, items, result)
        log.info(f"  Digest written for {name}")

    log.info("Done.")


if __name__ == "__main__":
    main()
