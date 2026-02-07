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
            "metadata": {} # Future proofing
        }
        
        # Ensure user exists (just in case, though Auth middleware should handle this usually, 
        # but for robust service calls we can upsert or assume existence)
        # We'll rely on the fact that they are talking to the bot, so they exist.
        
        response = supabase.table("archive").insert(payload).execute()
        return response.data[0] if response.data else None
        
    except Exception as e:
        logger.error(f"Failed to save note: {e}")
        return None
