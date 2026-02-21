"""iGPT Email Intelligence API wrapper — semantic email search and Q&A."""

import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.igpt.ai/v1/recall"
TIMEOUT = 12


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.IGPT_API_KEY}",
        "Content-Type": "application/json",
    }


async def ask(query: str) -> str | None:
    """Ask iGPT a natural-language question about the user's emails.

    Returns a cited answer string, or None on error / when disabled.
    """
    if not settings.igpt_enabled:
        return None

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{BASE_URL}/ask/",
                headers=_headers(),
                json={"query": query, "user": settings.IGPT_API_USER},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("response") or str(data)
    except httpx.TimeoutException:
        logger.warning("iGPT ask timed out for query: %s", query[:80])
        return None
    except Exception as e:
        logger.error("iGPT ask failed: %s", e)
        return None


async def search(
    query: str,
    date_from: str | None = None,
    date_to: str | None = None,
    max_results: int = 5,
) -> list[dict]:
    """Search emails semantically via iGPT.

    Returns a list of result dicts, or empty list on error / when disabled.
    """
    if not settings.igpt_enabled:
        return []

    try:
        body: dict = {
            "query": query,
            "user": settings.IGPT_API_USER,
            "max_results": max_results,
        }
        if date_from:
            body["date_from"] = date_from
        if date_to:
            body["date_to"] = date_to

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{BASE_URL}/search/",
                headers=_headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", data) if isinstance(data, dict) else data
    except httpx.TimeoutException:
        logger.warning("iGPT search timed out for query: %s", query[:80])
        return []
    except Exception as e:
        logger.error("iGPT search failed: %s", e)
        return []
