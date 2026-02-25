"""Content scanner — fetches new items from HN, Dev.to, RSS, GitHub, Reddit.

Deduplicates via the `content_seen` Supabase table so each item is only
processed once across runs.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.database import supabase

logger = logging.getLogger(__name__)

# --- RSS feeds for self-improvement scanning ---
IMPROVEMENT_RSS_FEEDS = [
    {"url": "https://simonwillison.net/atom/everything/", "source": "Simon Willison"},
    {"url": "https://www.anthropic.com/rss.xml", "source": "Anthropic Blog"},
    {"url": "https://www.techmeme.com/feed.xml", "source": "Techmeme"},
    {"url": "https://lilianweng.github.io/index.xml", "source": "Lil'Log"},
]

REDDIT_SUBS = ["LocalLLaMA", "MachineLearning", "selfhosted"]

DEVTO_TAGS = ["ai", "productivity", "automation", "llm"]

_HTTP_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

def _is_seen(source: str, external_id: str) -> bool:
    try:
        resp = (
            supabase.table("content_seen")
            .select("id")
            .eq("source", source)
            .eq("external_id", external_id)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as e:
        logger.warning(f"content_seen check failed: {e}")
        return False


def _mark_seen(source: str, external_id: str, url: str | None = None) -> None:
    try:
        supabase.table("content_seen").upsert(
            {"source": source, "external_id": external_id, "url": url or ""},
            on_conflict="source,external_id",
        ).execute()
    except Exception as e:
        logger.warning(f"content_seen write failed: {e}")


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

async def _fetch_hackernews(hours_back: int = 24) -> list[dict]:
    """Search HN Algolia for recent AI/automation stories."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp())
    params = {
        "query": "AI agent automation LLM telegram bot",
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff}",
        "hitsPerPage": 20,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get("https://hn.algolia.com/api/v1/search_by_date", params=params)
            resp.raise_for_status()
            data = resp.json()

        items = []
        for hit in data.get("hits", []):
            eid = str(hit.get("objectID", ""))
            if not eid or _is_seen("hackernews", eid):
                continue
            items.append({
                "source": "hackernews",
                "external_id": eid,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={eid}",
                "title": hit.get("title", ""),
                "summary": (hit.get("story_text") or "")[:300],
            })
        return items
    except Exception as e:
        logger.error(f"HN fetch failed: {e}")
        return []


async def _fetch_devto(hours_back: int = 24) -> list[dict]:
    """Fetch recent Dev.to articles by AI-related tags."""
    items = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            for tag in DEVTO_TAGS:
                try:
                    resp = await client.get(
                        "https://dev.to/api/articles",
                        params={"tag": tag, "top": 1, "per_page": 10},
                    )
                    resp.raise_for_status()
                    for article in resp.json():
                        eid = str(article.get("id", ""))
                        if not eid or _is_seen("devto", eid):
                            continue
                        items.append({
                            "source": "devto",
                            "external_id": eid,
                            "url": article.get("url", ""),
                            "title": article.get("title", ""),
                            "summary": (article.get("description") or "")[:300],
                        })
                except Exception as e:
                    logger.warning(f"Dev.to tag={tag} failed: {e}")
    except Exception as e:
        logger.error(f"Dev.to fetch failed: {e}")
    return items


async def _fetch_rss_blogs(hours_back: int = 48) -> list[dict]:
    """Fetch items from curated RSS feeds via feedparser."""
    import feedparser

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    items = []

    async def _parse_feed(feed_info: dict) -> list[dict]:
        try:
            parsed = await asyncio.to_thread(feedparser.parse, feed_info["url"])
            feed_items = []
            for entry in parsed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    entry_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if entry_dt < cutoff:
                        continue

                link = entry.get("link", "")
                eid = entry.get("id", link)
                if not eid or _is_seen("rss", str(eid)):
                    continue

                feed_items.append({
                    "source": "rss",
                    "external_id": str(eid),
                    "url": link,
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:300],
                })
            return feed_items
        except Exception as e:
            logger.warning(f"RSS feed {feed_info['source']} failed: {e}")
            return []

    results = await asyncio.gather(
        *[_parse_feed(f) for f in IMPROVEMENT_RSS_FEEDS],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def _fetch_github_trending() -> list[dict]:
    """Search GitHub for recently created AI agent repos."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    params = {
        "q": f"AI agent automation created:>{week_ago}",
        "sort": "stars",
        "order": "desc",
        "per_page": 10,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params=params,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            data = resp.json()

        items = []
        for repo in data.get("items", []):
            eid = str(repo.get("id", ""))
            if not eid or _is_seen("github", eid):
                continue
            items.append({
                "source": "github",
                "external_id": eid,
                "url": repo.get("html_url", ""),
                "title": repo.get("full_name", ""),
                "summary": (repo.get("description") or "")[:300],
            })
        return items
    except Exception as e:
        logger.error(f"GitHub fetch failed: {e}")
        return []


async def _fetch_reddit(hours_back: int = 24) -> list[dict]:
    """Fetch hot posts from relevant subreddits via public JSON API."""
    items = []
    headers = {"User-Agent": "AI-Super-Man-Bot/1.0"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            for sub in REDDIT_SUBS:
                try:
                    resp = await client.get(f"https://www.reddit.com/r/{sub}/hot.json", params={"limit": 10})
                    resp.raise_for_status()
                    posts = resp.json().get("data", {}).get("children", [])
                    for post in posts:
                        pdata = post.get("data", {})
                        eid = pdata.get("id", "")
                        if not eid or _is_seen("reddit", eid):
                            continue
                        # Skip stickied/meta posts
                        if pdata.get("stickied"):
                            continue
                        items.append({
                            "source": "reddit",
                            "external_id": eid,
                            "url": f"https://reddit.com{pdata.get('permalink', '')}",
                            "title": pdata.get("title", ""),
                            "summary": (pdata.get("selftext") or "")[:300],
                        })
                except Exception as e:
                    logger.warning(f"Reddit r/{sub} failed: {e}")
    except Exception as e:
        logger.error(f"Reddit fetch failed: {e}")
    return items


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def scan_all_sources() -> list[dict]:
    """Run all 5 source fetchers in parallel, flatten, mark as seen."""
    results = await asyncio.gather(
        _fetch_hackernews(),
        _fetch_devto(),
        _fetch_rss_blogs(),
        _fetch_github_trending(),
        _fetch_reddit(),
        return_exceptions=True,
    )

    all_items = []
    source_names = ["hackernews", "devto", "rss", "github", "reddit"]
    for name, result in zip(source_names, results):
        if isinstance(result, Exception):
            logger.error(f"Source {name} failed: {result}")
        elif isinstance(result, list):
            all_items.extend(result)

    # Mark all new items as seen
    for item in all_items:
        _mark_seen(item["source"], item["external_id"], item.get("url"))

    logger.info(f"Content scanner found {len(all_items)} new items")
    return all_items
