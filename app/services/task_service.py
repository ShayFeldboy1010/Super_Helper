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
    parsed_time = None
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
            # Try "YYYY-MM-DD HH:MM:SS" first (LLM format)
            try:
                parsed_dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
                target_date = parsed_dt.date()
                parsed_time = parsed_dt.time()
            except ValueError:
                # Try plain "YYYY-MM-DD"
                try:
                    target_date = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning(f"Invalid date format: {d}")

        if target_date:
            if parsed_time:
                due_at = datetime.combine(target_date, parsed_time).replace(tzinfo=TZ)
            elif data.get('time'):
                try:
                    t = datetime.strptime(data['time'], "%H:%M").time()
                    due_at = datetime.combine(target_date, t).replace(tzinfo=TZ)
                except ValueError:
                    due_at = datetime.combine(target_date, time(9, 0)).replace(tzinfo=TZ)
            else:
                due_at = datetime.combine(target_date, time(9, 0)).replace(tzinfo=TZ)

    payload = {
        "user_id": user_id,
        "title": data.get('title', 'Untitled Task'),
        "priority": data.get('priority', 0),
        "status": "pending"
    }
    if due_at:
        payload["due_at"] = due_at.isoformat()
    # Recurring tasks (Batch 7)
    if data.get('recurrence'):
        payload["recurrence"] = data['recurrence']
    # Effort estimation
    if data.get('effort'):
        payload["effort"] = data['effort']

    try:
        # Ensure user exists to avoid FK violation
        try:
             supabase.table("users").upsert({"telegram_id": user_id}, on_conflict="telegram_id").execute()
        except Exception as e:
            logger.warning(f"User upsert warning: {e}")

        response = supabase.table("tasks").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Supabase error: {e}")
        return None

def _parse_due_string(d: str) -> datetime | None:
    """Parse a date string into a TZ-aware datetime. Reused by create and edit."""
    d = d.strip().lower()
    now = datetime.now(TZ)
    today = now.date()

    if d == "today":
        return datetime.combine(today, time(9, 0)).replace(tzinfo=TZ)
    elif d == "tomorrow":
        return datetime.combine(today + timedelta(days=1), time(9, 0)).replace(tzinfo=TZ)
    else:
        try:
            parsed_dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
            return parsed_dt.replace(tzinfo=TZ)
        except ValueError:
            try:
                parsed_dt = datetime.strptime(d, "%Y-%m-%d")
                return datetime.combine(parsed_dt.date(), time(9, 0)).replace(tzinfo=TZ)
            except ValueError:
                return None


def _spawn_next_recurring(task: dict):
    """Create the next occurrence of a recurring task."""
    recurrence = task.get("recurrence")
    if not recurrence:
        return

    intervals = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}
    delta = intervals.get(recurrence)
    if not delta:
        return

    due_at = task.get("due_at")
    next_due = None
    if due_at:
        try:
            dt = datetime.fromisoformat(due_at)
            next_due = (dt + delta).isoformat()
        except (ValueError, TypeError):
            next_due = (datetime.now(TZ) + delta).isoformat()
    else:
        next_due = (datetime.now(TZ) + delta).isoformat()

    try:
        supabase.table("tasks").insert({
            "user_id": task["user_id"],
            "title": task["title"],
            "priority": task.get("priority", 0),
            "status": "pending",
            "due_at": next_due,
            "recurrence": recurrence,
        }).execute()
        logger.info(f"Spawned next {recurrence} occurrence of '{task['title']}'")
    except Exception as e:
        logger.error(f"Failed to spawn recurring task: {e}")


def _match_task(tasks: list[dict], title_query: str) -> dict | None:
    """Find best matching task by title. Tries exact substring, then word overlap."""
    query_lower = title_query.lower()

    # Tier 1: exact substring match
    for t in tasks:
        title_lower = t["title"].lower()
        if query_lower in title_lower or title_lower in query_lower:
            return t

    # Tier 2: word overlap (2+ words or single-word title)
    query_words = {w for w in query_lower.split() if len(w) > 1}
    best_match = None
    best_overlap = 0
    for t in tasks:
        title_words = {w for w in t["title"].lower().split() if len(w) > 1}
        overlap = len(query_words & title_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = t

    return best_match if best_overlap > 0 else None


async def complete_all_tasks(user_id: int) -> int:
    """Mark ALL pending tasks as completed. Returns count of tasks completed."""
    try:
        resp = (
            supabase.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        tasks = resp.data or []
        if not tasks:
            return 0

        count = 0
        for t in tasks:
            try:
                supabase.table("tasks").update({"status": "completed"}).eq("id", t["id"]).execute()
                count += 1
                logger.info(f"Completed: {t['title']} (id={t['id']})")
                # Auto-spawn next occurrence for recurring tasks
                _spawn_next_recurring(t)
            except Exception as e:
                logger.error(f"Failed to complete task {t['id']}: {e}")

        return count
    except Exception as e:
        logger.error(f"Error completing all tasks: {e}")
        return 0


async def complete_task(user_id: int, title_query: str) -> dict | None:
    """Find a pending task by title match and mark it as completed."""
    try:
        resp = (
            supabase.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        tasks = resp.data or []
        if not tasks:
            logger.info(f"No pending tasks found for user {user_id}")
            return None

        match = _match_task(tasks, title_query)
        if not match:
            logger.info(f"No task matched '{title_query}' from {len(tasks)} pending tasks")
            return None

        # Update and verify
        update_resp = supabase.table("tasks").update({"status": "completed"}).eq("id", match["id"]).execute()
        if update_resp.data:
            logger.info(f"Task completed: {match['title']} (id={match['id']})")
            # Auto-spawn next occurrence for recurring tasks
            _spawn_next_recurring(match)
            return match
        else:
            logger.error(f"Task update returned no data for id={match['id']}")
            return None

    except Exception as e:
        logger.error(f"Error completing task: {e}")
        return None


async def delete_task(user_id: int, title_query: str) -> dict | None:
    """Find a pending task by title match and delete it."""
    try:
        resp = (
            supabase.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        tasks = resp.data or []
        if not tasks:
            return None

        match = _match_task(tasks, title_query)
        if not match:
            logger.info(f"No task matched '{title_query}' for delete")
            return None

        # Delete and verify
        del_resp = supabase.table("tasks").delete().eq("id", match["id"]).execute()
        if del_resp.data:
            logger.info(f"Task deleted: {match['title']} (id={match['id']})")
            return match
        else:
            logger.error(f"Task delete returned no data for id={match['id']}")
            return None

    except Exception as e:
        logger.error(f"Error deleting task: {e}")
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


async def edit_task(user_id: int, title_query: str, updates: dict) -> dict | None:
    """Find a pending task by title match and apply updates."""
    try:
        resp = (
            supabase.table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        tasks = resp.data or []
        if not tasks:
            return None

        match = _match_task(tasks, title_query)
        if not match:
            logger.info(f"No task matched '{title_query}' for edit")
            return None

        payload = {}
        if "title" in updates:
            payload["title"] = updates["title"]
        if "due_date" in updates:
            parsed = _parse_due_string(updates["due_date"])
            if parsed:
                payload["due_at"] = parsed.isoformat()
        if "priority" in updates:
            payload["priority"] = updates["priority"]

        if not payload:
            return match  # nothing to change

        update_resp = supabase.table("tasks").update(payload).eq("id", match["id"]).execute()
        if update_resp.data:
            logger.info(f"Task edited: {match['title']} -> {payload}")
            return update_resp.data[0]
        return None

    except Exception as e:
        logger.error(f"Error editing task: {e}")
        return None
