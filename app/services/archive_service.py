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
            "metadata": {}
        }

        response = supabase.table("archive").insert(payload).execute()
        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Failed to save note: {e}")
        return None


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
        payload = {
            "user_id": user_id,
            "content": summary,
            "tags": tags or [],
            "metadata": {
                "type": "url",
                "url": url,
                "title": title,
                "key_points": key_points,
                "original_content_preview": content[:500],
            },
        }
        response = supabase.table("archive").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Failed to save URL knowledge: {e}")
        return None
