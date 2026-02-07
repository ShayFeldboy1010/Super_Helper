from app.core.database import supabase
from app.models.schemas import TaskCreate
from datetime import datetime, timedelta, time
import logging
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Jerusalem")

async def create_task(user_id: int, task_data):
    # task_data can be dict or Pydantic model - normalize it
    if hasattr(task_data, 'model_dump'):
        data = task_data.model_dump()
    elif isinstance(task_data, dict):
        data = task_data
    else:
        data = task_data.__dict__
    
    # Calculate due_at
    due_at = None
    if data.get('due_date'):
        target_date = None
        now = datetime.now(TZ)
        today = now.date()
        
        d = data.get('due_date', '').lower().strip()
        if d == "today":
            target_date = today
        elif d == "tomorrow":
            target_date = today + timedelta(days=1)
        else:
            try:
                target_date = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Invalid date format: {d}")
        
        if target_date:
            if data.get('time'):
                try:
                    t = datetime.strptime(data['time'], "%H:%M").time()
                    due_at = datetime.combine(target_date, t).replace(tzinfo=TZ)
                except ValueError:
                    due_at = datetime.combine(target_date, time(9, 0)).replace(tzinfo=TZ) # Default 9 AM
            else:
                # If date is strictly future, default to 9am. If today, maybe user means EOD? sticking to 9am or noon is safe.
                due_at = datetime.combine(target_date, time(9, 0)).replace(tzinfo=TZ)

    payload = {
        "user_id": user_id,
        "title": data.get('title', 'Untitled Task'),
        "priority": data.get('priority', 0),
        "status": "pending"
    }
    if due_at:
        payload["due_at"] = due_at.isoformat()

    try:
        # Ensure user exists to avoid FK violation
        # We try to Insert user, ignoring if already exists
        try:
             supabase.table("users").upsert({"telegram_id": user_id}, on_conflict="telegram_id").execute()
        except Exception as e:
            logger.warning(f"User upsert warning: {e}")

        response = supabase.table("tasks").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        return None

async def get_overdue_tasks(user_id: int):
    try:
        now_iso = datetime.now(TZ).isoformat()
        response = supabase.table("tasks").select("*").eq("user_id", user_id).eq("status", "pending").lt("due_at", now_iso).execute()
        return response.data
    except Exception as e:
        logger.error(f"Error fetching overdue tasks: {e}")
        return []

async def get_pending_tasks(user_id: int, limit: int = 5):
    try:
        response = supabase.table("tasks").select("*").eq("user_id", user_id).eq("status", "pending").order("priority", desc=True).limit(limit).execute()
        return response.data
    except Exception as e:
        logger.error(f"Error fetching pending tasks: {e}")
        return []
