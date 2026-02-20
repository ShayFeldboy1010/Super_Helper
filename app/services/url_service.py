"""URL content extraction, summarization, and auto-tagging."""

import re
import logging

import httpx
from bs4 import BeautifulSoup

from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')


def extract_urls(text: str) -> list[str]:
    """Extract URLs from text using regex."""
    return URL_PATTERN.findall(text)


async def fetch_url_content(url: str) -> dict:
    """Fetch URL and extract readable content with BeautifulSoup."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
            resp = await http.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else url

        # Extract paragraph text
        paragraphs = soup.find_all("p")
        content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        content = content[:3000]  # Truncate for LLM context

        return {"url": url, "title": title, "content": content, "error": None}
    except Exception as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        return {"url": url, "title": url, "content": "", "error": str(e)}


async def summarize_and_tag(url: str, title: str, content: str) -> dict:
    """Use Groq LLM to summarize content and generate tags."""
    if not content:
        return {"summary": "Couldn't extract content from the link.", "tags": [], "key_points": []}

    prompt = (
        "You are a content analyst. You received an article/page from the web.\n"
        "Return JSON with:\n"
        '- "summary": summary in English (2-3 sentences)\n'
        '- "tags": list of English tags (3-5 relevant tags)\n'
        '- "key_points": 2-3 key points in English\n\n'
        f"Title: {title}\nURL: {url}\n\nContent:\n{content}"
    )

    chat_completion = await llm_call(
        messages=[
            {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY + "\n\nYou are a content analyst. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        timeout=10,

    )

    if not chat_completion:
        return {"summary": f"Saved the link: {title}", "tags": [], "key_points": []}

    import json
    result = json.loads(chat_completion.choices[0].message.content)
    return {
        "summary": result.get("summary", ""),
        "tags": result.get("tags", []),
        "key_points": result.get("key_points", []),
    }
