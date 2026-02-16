from app.core.database import supabase
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

async def save_note(user_id: int, content: str, tags: Optional[List[str]] = None):
    try:
        payload = {
            "user_id": user_id,
            "content": content,
            "tags": tags or [],
        }

        response = supabase.table("archive").insert(payload).execute()
        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Failed to save note: {e}")
        return None


async def search_archive(
    user_id: int,
    query: str,
    tags: Optional[List[str]] = None,
    limit: int = 10,
    since: Optional[str] = None,
) -> List[dict]:
    """Full-text search on the archive table. Requires fts column + GIN index.

    Args:
        since: ISO date string (YYYY-MM-DD) to filter results after this date.
    """
    try:
        results = []

        # FTS search
        if query and query.strip():
            import re as _re
            words = [_re.sub(r'[^\w\u0590-\u05FF]', '', w) for w in query.strip().split()]
            words = [w for w in words if len(w) > 1]
            if words:
                ts_query = " | ".join(words)
                try:
                    q = (
                        supabase.table("archive")
                        .select("content, tags, created_at")
                        .eq("user_id", user_id)
                        .text_search("fts", ts_query)
                    )
                    if since:
                        q = q.gte("created_at", f"{since}T00:00:00")
                    fts_resp = q.order("created_at", desc=True).limit(limit).execute()
                    results.extend(fts_resp.data or [])
                except Exception as e:
                    logger.warning(f"Archive FTS failed, falling back to basic: {e}")
                    q = (
                        supabase.table("archive")
                        .select("content, tags, created_at")
                        .eq("user_id", user_id)
                        .ilike("content", f"%{words[0]}%")
                    )
                    if since:
                        q = q.gte("created_at", f"{since}T00:00:00")
                    fallback = q.order("created_at", desc=True).limit(limit).execute()
                    results.extend(fallback.data or [])

        # Time-only search (no query text, just "what did I save this week")
        if not results and since and not (query and query.strip()):
            time_resp = (
                supabase.table("archive")
                .select("content, tags, created_at")
                .eq("user_id", user_id)
                .gte("created_at", f"{since}T00:00:00")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            results.extend(time_resp.data or [])

        # Optional tag filter
        if tags and not results:
            q = (
                supabase.table("archive")
                .select("content, tags, created_at")
                .eq("user_id", user_id)
                .overlaps("tags", tags)
            )
            if since:
                q = q.gte("created_at", f"{since}T00:00:00")
            tag_resp = q.order("created_at", desc=True).limit(limit).execute()
            results.extend(tag_resp.data or [])

        return results[:limit]
    except Exception as e:
        logger.error(f"Archive search error: {e}")
        return []


async def save_url_knowledge(
    user_id: int,
    url: str,
    title: str,
    content: str,
    summary: str,
    tags: List[str],
    key_points: List[str],
) -> Optional[dict]:
    """Save URL content as a knowledge entry in the archive."""
    try:
        full_content = f"{title}\n\n{summary}"
        if key_points:
            full_content += "\n\n" + "\n".join(f"- {kp}" for kp in key_points)
        full_content += f"\n\nSource: {url}"

        payload = {
            "user_id": user_id,
            "content": full_content,
            "tags": tags or [],
        }
        response = supabase.table("archive").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Failed to save URL knowledge: {e}")
        return None
