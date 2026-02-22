"""iGPT Email Intelligence API wrapper — semantic email search and Q&A.

Uses the official igptai SDK for reliable authentication and request handling.
"""

import asyncio
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_client():
    """Lazy-init iGPT SDK client."""
    from igptai import IGPT

    return IGPT(
        api_key=settings.IGPT_API_KEY,
        user=settings.IGPT_API_USER,
    )


async def ask(query: str) -> str | None:
    """Ask iGPT a natural-language question about the user's emails.

    Returns a cited answer string, or None on error / when disabled.
    """
    if not settings.igpt_enabled:
        return None

    try:
        logger.info("iGPT ask: %s", query[:80])
        client = _get_client()
        res = await asyncio.to_thread(
            client.recall.ask,
            input=query,
            quality="cef-1-normal",
        )
        if res is None:
            logger.warning("iGPT ask returned None")
            return None
        if isinstance(res, dict) and res.get("error"):
            logger.warning("iGPT ask error: %s", res["error"])
            return None
        if isinstance(res, dict):
            tokens = res.get("usage", {}).get("total_tokens", "?")
            logger.info("iGPT ask success — tokens: %s", tokens)
            return res.get("output") or None
        return str(res) if res else None
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
        client = _get_client()
        kwargs = {"query": query, "max_results": max_results}
        if date_from:
            kwargs["date_from"] = date_from
        if date_to:
            kwargs["date_to"] = date_to

        res = await asyncio.to_thread(client.recall.search, **kwargs)
        if res is None:
            return []
        if isinstance(res, dict) and res.get("error"):
            logger.warning("iGPT search error: %s", res["error"])
            return []
        if isinstance(res, dict):
            return res.get("results", [])
        return res if isinstance(res, list) else []
    except Exception as e:
        logger.error("iGPT search failed: %s", e)
        return []
