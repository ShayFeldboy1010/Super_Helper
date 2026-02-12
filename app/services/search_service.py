"""Web search via Brave API (primary) with DuckDuckGo HTML fallback."""
import logging

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


async def brave_search(query: str, max_results: int = 5) -> list[dict]:
    """Search via Brave Search API. Requires BRAVE_SEARCH_API_KEY."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                BRAVE_URL,
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": settings.BRAVE_SEARCH_API_KEY,
                },
            )
            resp.raise_for_status()

        data = resp.json()
        results = []
        for item in (data.get("web", {}).get("results", []))[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
                "url": item.get("url", ""),
            })
        return results

    except Exception as e:
        logger.error(f"Brave search error: {e}")
        return []


async def ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo HTML scraping — no API key needed."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.post(
                DDG_URL,
                data={"q": query, "b": ""},
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": "https://duckduckgo.com/",
                },
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for r in soup.select(".result"):
            title_tag = r.select_one(".result__a")
            snippet_tag = r.select_one(".result__snippet")
            if not title_tag:
                continue

            href = title_tag.get("href", "")
            title = title_tag.get_text(strip=True)
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            if title and snippet:
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "url": href,
                })

            if len(results) >= max_results:
                break

        return results

    except Exception as e:
        logger.error(f"DDG search error: {e}")
        return []


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Try Brave first (if key set), fall back to DDG."""
    if settings.BRAVE_SEARCH_API_KEY:
        results = await brave_search(query, max_results)
        if results:
            return results
    return await ddg_search(query, max_results)


def format_search_results(results: list[dict]) -> str:
    """Format search results as context string for LLM."""
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n\n".join(lines)
