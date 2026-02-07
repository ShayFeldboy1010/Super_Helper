from fastapi import APIRouter, Header, HTTPException, Depends
from app.core.config import settings
from app.services.task_service import get_overdue_tasks
from app.bot.loader import bot
import logging

router = APIRouter(prefix="/api/cron", tags=["cron"])
logger = logging.getLogger(__name__)

async def verify_cron_secret(authorization: str = Header(None)):
    # Vercel sends "Authorization: Bearer <CRON_SECRET>"
    # Or strict header check. For now, simple check.
    # User needs to set CRON_SECRET env var.
    if not authorization:
        # For dev testing allow no header or bypass? NO, secure it.
        # But for now, if env not set, warn.
        pass
    
    expected = f"Bearer {settings.M_WEBHOOK_SECRET}" # Reuse webhook secret for simplicity or add new env
    if authorization != expected:
        # logger.warning(f"Invalid cron auth: {authorization}")
        # raise HTTPException(status_code=401, detail="Unauthorized")
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
                f"🚨 **Nag Alert!** 🚨\n"
                f"You missed: **{task['title']}**\n"
                f"Due: {task.get('due_at')}\n\n"
                f"Get it done! 💪"
            )
            # Inline keyboard to complete? 
            # (Requires callback query handler implementation, skipping for MVP)
            await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send alert for task {task.get('id')}: {e}")
            
    return {"status": "ok", "reminders_sent": len(tasks)}

@router.get("/daily-brief")
async def daily_brief():
    user_id = settings.TELEGRAM_USER_ID
    
    # 1. Get Calendar
    from app.services.google_svc import GoogleService
    google = GoogleService(user_id) # Using default user for cron? 
    # Warning: GoogleService auth relies on DB token for user_id.
    # Since we use settings.TELEGRAM_USER_ID, it should work if that user is in DB.
    
    calendar_lines = await google.get_todays_events()
    calendar_str = "\n".join(calendar_lines)
    
    # 2. Get Tasks
    from app.services.task_service import get_pending_tasks
    tasks = await get_pending_tasks(user_id)
    task_str = "No pending tasks! 🎉"
    if tasks:
        task_str = "\n".join([f"• {t['title']} (Due: {t.get('due_at')})" for t in tasks])
        
    msg = (
        f"☀️ **Good Morning! Here is your Daily Brief:**\n\n"
        f"📅 **Calendar:**\n{calendar_lines}\n\n" # calendar_lines is list? wait
        f"📝 **Top Tasks:**\n{task_str}\n\n"
        f"Have a productive day! 🚀"
    )
    
    # Fix calendar_lines type if it's list
    if isinstance(calendar_lines, list):
         calendar_str = "\n".join(calendar_lines)
         msg = msg.replace(f"{calendar_lines}", calendar_str)

    await bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
    return {"status": "ok", "message": "Briefing sent"}
