"""AI news aggregation from curated RSS feeds."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import feedparser

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "source": "TechCrunch"},
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "source": "The Verge"},
    {"url": "https://www.technologyreview.com/feed/", "source": "MIT Tech Review"},
    {"url": "https://openai.com/blog/rss.xml", "source": "OpenAI Blog"},
]


async def _fetch_single_feed(feed_info: dict, hours_back: int) -> list[dict]:
    """Fetch and parse a single RSS feed in a thread pool."""
    try:
        parsed = await asyncio.to_thread(feedparser.parse, feed_info["url"])
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        items = []

        for entry in parsed.entries:
            # Try to parse published date
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                entry_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if entry_dt < cutoff:
                    continue

            items.append({
                "title": entry.get("title", ""),
                "source": feed_info["source"],
                "link": entry.get("link", ""),
                "summary": (entry.get("summary", "") or "")[:200],
            })

        return items
    except Exception as e:
        logger.error(f"Failed to fetch feed {feed_info['source']}: {e}")
        return []


async def fetch_ai_news(max_items: int = 10, hours_back: int = 24) -> list[dict]:
    """Fetch AI news from curated RSS feeds, all in parallel. Cached 5min."""
    from app.core.cache import cache_get, cache_set

    cache_key = f"ai_news:{max_items}:{hours_back}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    results = await asyncio.gather(
        *[_fetch_single_feed(feed, hours_back) for feed in RSS_FEEDS],
        return_exceptions=True,
    )

    all_items = []
    for result in results:
        if isinstance(result, list):
            all_items.extend(result)

    # Sort by title (proxy for recency when dates unavailable) and limit
    items = all_items[:max_items]
    cache_set(cache_key, items, 300)
    return items
