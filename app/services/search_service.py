"""Web search via DuckDuckGo HTML — no API key needed."""
import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo and return results."""
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

            # Extract actual URL from DuckDuckGo redirect
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
        logger.error(f"Web search error: {e}")
        return []


def format_search_results(results: list[dict]) -> str:
    """Format search results as context string for LLM."""
    if not results:
        return "לא נמצאו תוצאות חיפוש."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}")
    return "\n\n".join(lines)
