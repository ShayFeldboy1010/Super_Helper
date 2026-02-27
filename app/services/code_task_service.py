"""Code task service — manages code_tasks table, the bridge between
Telegram approval and the local Mac agent running Claude Code."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.database import supabase

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Jerusalem")

INSTRUCTION_TEMPLATE = """\
Implement the following improvement to the AI Super Man Telegram bot:

Title: {title}
Description: {description}
Type: {proposal_type}

Context: This is a Python/FastAPI/aiogram Telegram bot using Supabase and Gemini LLM.
Key directories: app/services/, app/bot/routers/, app/core/

Rules:
- Follow existing code patterns (async/await, try/except with fallbacks, Hebrew UI)
- Do NOT break existing functionality
- Keep changes minimal and focused
- Add logging where appropriate
- Test imports work correctly
"""


async def create_code_task(
    user_id: int,
    instruction: str,
    source: str = "manual",
    proposal_id: str | None = None,
) -> dict | None:
    """Create a new code task for the local agent to pick up."""
    try:
        row = {
            "user_id": user_id,
            "instruction": instruction,
            "source": source,
            "status": "pending",
        }
        if proposal_id:
            row["proposal_id"] = proposal_id
        resp = supabase.table("code_tasks").insert(row).execute()
        if resp.data:
            return resp.data[0]
    except Exception as e:
        logger.error(f"Failed to create code task: {e}")
    return None


async def get_task_status(task_id: str) -> dict | None:
    """Get status of a specific code task."""
    try:
        resp = (
            supabase.table("code_tasks")
            .select("*")
            .eq("id", task_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as e:
        logger.error(f"Failed to get code task status: {e}")
        return None


async def get_recent_tasks(user_id: int, limit: int = 5) -> list[dict]:
    """Get recent code tasks for the user."""
    try:
        resp = (
            supabase.table("code_tasks")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"Failed to get recent code tasks: {e}")
        return []


async def get_last_task_context(user_id: int) -> str:
    """Get the last completed code task's instruction + result as context for follow-up tasks."""
    try:
        resp = (
            supabase.table("code_tasks")
            .select("instruction,result_summary,claude_output,status")
            .eq("user_id", user_id)
            .in_("status", ["completed", "failed"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return ""
        task = resp.data[0]
        # Use result_summary (short) + first part of claude_output for context
        output = task.get("claude_output") or task.get("result_summary") or ""
        output = output[:2000]  # Cap context size
        instruction = (task.get("instruction") or "")[:500]
        return (
            f"=== Previous code task ===\n"
            f"Instruction: {instruction}\n"
            f"Status: {task['status']}\n"
            f"Output: {output}\n"
            f"=== End previous task ===\n\n"
        )
    except Exception as e:
        logger.warning(f"Failed to get last task context: {e}")
        return ""


async def get_completed_tasks_since(user_id: int, minutes: int = 35) -> list[dict]:
    """Get tasks completed in the last N minutes (for cron notification)."""
    try:
        cutoff = (datetime.now(TZ) - timedelta(minutes=minutes)).isoformat()
        resp = (
            supabase.table("code_tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "completed")
            .gte("completed_at", cutoff)
            .order("completed_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"Failed to get completed tasks: {e}")
        return []


async def approve_proposal(user_id: int, proposal_index: int) -> dict | None:
    """Approve the Nth pending proposal from today, create a code task for it."""
    try:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        resp = (
            supabase.table("improvement_proposals")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .gte("created_at", f"{today_str}T00:00:00")
            .order("created_at", desc=False)
            .execute()
        )
        proposals = resp.data or []
        if proposal_index < 1 or proposal_index > len(proposals):
            return None

        proposal = proposals[proposal_index - 1]

        # Mark proposal as approved
        supabase.table("improvement_proposals").update(
            {"status": "approved"}
        ).eq("id", proposal["id"]).execute()

        # Create code task with detailed instruction
        instruction = INSTRUCTION_TEMPLATE.format(
            title=proposal["title"],
            description=proposal["description"],
            proposal_type=proposal["proposal_type"],
        )

        task = await create_code_task(
            user_id=user_id,
            instruction=instruction,
            source="proposal",
            proposal_id=proposal["id"],
        )
        return task

    except Exception as e:
        logger.error(f"Failed to approve proposal: {e}")
        return None


async def reject_proposal(user_id: int, proposal_index: int) -> bool:
    """Reject the Nth pending proposal from today."""
    try:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        resp = (
            supabase.table("improvement_proposals")
            .select("id")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .gte("created_at", f"{today_str}T00:00:00")
            .order("created_at", desc=False)
            .execute()
        )
        proposals = resp.data or []
        if proposal_index < 1 or proposal_index > len(proposals):
            return False

        proposal_id = proposals[proposal_index - 1]["id"]
        supabase.table("improvement_proposals").update(
            {"status": "rejected"}
        ).eq("id", proposal_id).execute()
        return True

    except Exception as e:
        logger.error(f"Failed to reject proposal: {e}")
        return False


def _extract_instruction_title(instruction: str) -> str:
    """Get a meaningful first line from the instruction, skipping template boilerplate."""
    for line in instruction.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(("Implement the following", "Context:", "Key directories:", "Rules:")):
            continue
        if line.startswith("Title: "):
            return line[7:][:100]
        if line.startswith("Description: "):
            return line[13:][:100]
        if line.startswith("New instruction from user: "):
            return line[27:][:100]
        if line.startswith("=== Previous code task ===") or line.startswith("=== End previous task ==="):
            continue
        if line.startswith(("Instruction:", "Status:", "Output:")):
            continue
        return line[:100]
    return instruction[:100]


def _format_task_duration(task: dict) -> str:
    """Compute a human-readable duration or elapsed time for a task."""
    started = task.get("started_at")
    completed = task.get("completed_at")
    if not started:
        return ""
    try:
        start_dt = datetime.fromisoformat(started)
        if completed:
            end_dt = datetime.fromisoformat(completed)
            delta = end_dt - start_dt
        else:
            # In-progress: elapsed since started
            end_dt = datetime.now(TZ)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=ZoneInfo("UTC"))
            delta = end_dt - start_dt
        total_secs = int(delta.total_seconds())
        if total_secs < 0:
            return ""
        m, s = divmod(total_secs, 60)
        if m > 0:
            return f"{m}m {s:02d}s"
        return f"{s}s"
    except Exception:
        return ""


def format_task_status_message(task: dict) -> str:
    """Format a code task into a Hebrew status message."""
    status_emoji = {
        "pending": "⏳",
        "in_progress": "🔄",
        "completed": "✅",
        "failed": "❌",
    }
    status = task.get("status", "")
    emoji = status_emoji.get(status, "❓")

    # Meaningful title from instruction
    instruction = task.get("instruction") or ""
    title = _extract_instruction_title(instruction)

    duration = _format_task_duration(task)
    dur_str = f" ({duration})" if duration else ""

    lines = [f"{emoji} {title}"]
    lines.append(f"Status: {status}{dur_str}")

    if task.get("git_commit_hash"):
        lines.append(f"🔗 Commit: {task['git_commit_hash'][:8]}")
    if task.get("result_summary") and status in ("completed", "failed"):
        # Show a short excerpt of the result
        summary = task["result_summary"][:200].strip()
        if summary:
            lines.append(f"Result: {summary}")

    lines.append(f"ID: {task['id'][:8]}")

    return "\n".join(lines)


def format_recent_tasks_message(tasks: list[dict]) -> str:
    """Format multiple code tasks into a summary message."""
    if not tasks:
        return "אין משימות קוד אחרונות."

    lines = ["📋 משימות קוד אחרונות:\n"]
    for task in tasks:
        status = task.get("status", "")
        emoji = {"pending": "⏳", "in_progress": "🔄", "completed": "✅", "failed": "❌"}.get(status, "❓")

        instruction = task.get("instruction") or ""
        title = _extract_instruction_title(instruction)[:60]

        duration = _format_task_duration(task)
        dur_str = f" ({duration})" if duration else ""

        commit = f" | {task['git_commit_hash'][:8]}" if task.get("git_commit_hash") else ""
        lines.append(f"{emoji} {title}{dur_str}{commit}")

    return "\n".join(lines)
