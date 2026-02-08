import re
import logging

import httpx
from bs4 import BeautifulSoup
from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)

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
        return {"summary": "לא הצלחתי לחלץ תוכן מהקישור.", "tags": [], "key_points": []}

    try:
        prompt = (
            "אתה מנתח תוכן. קיבלת כתבה/דף מהאינטרנט.\n"
            "החזר JSON עם:\n"
            '- "summary": סיכום בעברית (2-3 משפטים)\n'
            '- "tags": רשימת תגיות באנגלית (3-5 תגיות רלוונטיות)\n'
            '- "key_points": 2-3 נקודות מפתח בעברית\n\n'
            f"כותרת: {title}\nURL: {url}\n\nתוכן:\n{content}"
        )

        chat_completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": "אתה מנתח תוכן מקצועי. החזר רק JSON תקין."},
                {"role": "user", "content": prompt},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        import json
        result = json.loads(chat_completion.choices[0].message.content)
        return {
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "key_points": result.get("key_points", []),
        }
    except Exception as e:
        logger.error(f"LLM summarization error: {e}")
        return {"summary": f"שמרתי את הקישור: {title}", "tags": [], "key_points": []}
