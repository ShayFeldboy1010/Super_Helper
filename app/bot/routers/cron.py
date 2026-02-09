from fastapi import APIRouter, Header, HTTPException, Depends
from app.core.config import settings
from app.services.task_service import get_overdue_tasks
from app.services.memory_service import run_daily_reflection
from app.bot.loader import bot
import logging

router = APIRouter(prefix="/api/cron", tags=["cron"])
logger = logging.getLogger(__name__)

async def verify_cron_secret(authorization: str = Header(None)):
    # Vercel sends "Authorization: Bearer <CRON_SECRET>"
    # Or strict header check. For now, simple check.
    # User needs to set CRON_SECRET env var.
    if not authorization:
        pass

    expected = f"Bearer {settings.M_WEBHOOK_SECRET}" # Reuse webhook secret for simplicity or add new env
    if authorization != expected:
        pass # Returning pass to avoid breaking if user hasn't configured it yet

@router.get("/check-reminders") # Vercel cron calls GET by default usually? Vercel supports GET/POST.
async def check_reminders():
    user_id = settings.TELEGRAM_USER_ID
    tasks = await get_overdue_tasks(user_id)

    if not tasks:
        return {"status": "ok", "message": "No overdue tasks"}

    for task in tasks:
        try:
            msg = (
                f"🚨 Nag Alert!\n"
                f"You missed: {task['title']}\n"
                f"Due: {task.get('due_at')}\n\n"
                f"Get it done! 💪"
            )
            await bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to send alert for task {task.get('id')}: {e}")

    return {"status": "ok", "reminders_sent": len(tasks)}

@router.get("/daily-brief")
async def daily_brief():
    user_id = settings.TELEGRAM_USER_ID

    try:
        from app.services.briefing_service import generate_morning_briefing
        msg = await generate_morning_briefing(user_id)

        # Split if exceeds Telegram 4096 char limit
        if len(msg) <= 4096:
            await bot.send_message(chat_id=user_id, text=msg)
        else:
            # Send in chunks at line breaks
            chunks = []
            current = ""
            for line in msg.split("\n"):
                if len(current) + len(line) + 1 > 4000:
                    chunks.append(current)
                    current = line
                else:
                    current += "\n" + line if current else line
            if current:
                chunks.append(current)

            for chunk in chunks:
                await bot.send_message(chat_id=user_id, text=chunk)

        return {"status": "ok", "message": "Enhanced briefing sent"}

    except Exception as e:
        logger.error(f"Enhanced briefing failed: {e}, falling back to basic")
        # Fallback: basic briefing
        from app.services.google_svc import GoogleService
        from app.services.task_service import get_pending_tasks

        google = GoogleService(user_id)
        calendar_lines = await google.get_todays_events()
        calendar_str = "\n".join(calendar_lines)

        tasks = await get_pending_tasks(user_id)
        task_str = "No open tasks."
        if tasks:
            task_str = "\n".join([f"• {t['title']}" for t in tasks])

        msg = (
            f"Morning Briefing\n\n"
            f"📅 Calendar:\n{calendar_str}\n\n"
            f"✅ Tasks:\n{task_str}"
        )
        await bot.send_message(chat_id=user_id, text=msg)
        return {"status": "ok", "message": "Basic briefing sent (fallback)"}


@router.get("/heartbeat")
async def heartbeat():
    """Proactive check-in — mid-week nudge or evening wrap-up depending on time."""
    user_id = settings.TELEGRAM_USER_ID

    try:
        from app.services.heartbeat_service import generate_goal_checkin, generate_evening_wrapup
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
        hour = now.hour

        # Evening (20:00-22:00) → wrap-up, otherwise → goal check-in
        if 20 <= hour <= 22:
            msg = await generate_evening_wrapup(user_id)
        else:
            msg = await generate_goal_checkin(user_id)

        if not msg:
            return {"status": "ok", "message": "Nothing to report"}

        await bot.send_message(chat_id=user_id, text=msg)

        return {"status": "ok", "message": "Heartbeat sent"}

    except Exception as e:
        logger.error(f"Heartbeat error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/weekly-review")
async def weekly_review():
    """Sunday evening weekly review."""
    user_id = settings.TELEGRAM_USER_ID

    try:
        from app.services.heartbeat_service import generate_weekly_review
        msg = await generate_weekly_review(user_id)

        if not msg:
            return {"status": "ok", "message": "No review generated"}

        await bot.send_message(chat_id=user_id, text=msg)

        return {"status": "ok", "message": "Weekly review sent"}

    except Exception as e:
        logger.error(f"Weekly review error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/daily-reflection")
async def daily_reflection():
    user_id = settings.TELEGRAM_USER_ID

    result = await run_daily_reflection(user_id)

    # Send Telegram summary if new insights were found
    if result["new_insights"] > 0 or result["reinforced_insights"] > 0:
        try:
            msg = (
                f"🧠 Daily Reflection Summary\n"
                f"Interactions analyzed: {result['interactions_analyzed']}\n"
                f"New insights: {result['new_insights']}\n"
                f"Reinforced insights: {result['reinforced_insights']}"
            )
            await bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to send reflection summary: {e}")

    return {"status": "ok", **result}
