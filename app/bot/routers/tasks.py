from aiogram import Router, F, types
from aiogram.filters import Command
from app.services.router_service import route_intent
from app.services.task_service import create_task
# We will import other services here as we build them (calendar_svc, archive_svc)
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(f"👋 Hi! I'm your AI Brain. Tell me what's on your mind.")

@router.message(F.text)
async def handle_router_message(message: types.Message):
    if not message.text:
        return

    # User feedback: Processing
    status_msg = await message.answer("🧠 Thinking...")
    
    try:
        # 1. Route Intent
        intent = await route_intent(message.text)
        action_type = intent.classification.action_type
        
        # 2. Dispatch
        if action_type == "task":
            await handle_task(message, intent, status_msg)
        elif action_type == "calendar":
            await handle_calendar(message, intent, status_msg)
        elif action_type == "note":
            await handle_note(message, intent, status_msg)
        elif action_type == "query":
            await handle_query(message, intent, status_msg)
        else:
            await status_msg.edit_text("🤷 I'm not sure what to do with that.")
            
    except Exception as e:
        logger.error(f"Handler Error: {e}")
        await status_msg.edit_text("❌ Error processing request.")

async def handle_task(message: types.Message, intent, status_msg):
    if not intent.task:
        await status_msg.edit_text("❌ Error: Identified as task but missing details.")
        return

    user_id = message.from_user.id
    # Map the new TaskPayload to the schema expected by create_task if needed
    # Or update create_task schema. For now we adapter specific fields.
    
    # Quick adapter: TaskPayload -> TaskCreate (dict/schema)
    task_data = intent.task.model_dump()
    
    task = await create_task(user_id, task_data) # task_service expects a dict or object? It expects generic dict/schema
    
    if task:
        due_str = f"\n📅 Due: {task.get('due_at')}" if task.get('due_at') else ""
        text = (
            f"✅ **Task Created**\n"
            f"📝 {task['title']}"
            f"{due_str}\n"
            f"🔥 Priority: {task['priority']}"
        )
        await status_msg.edit_text(text, parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ Failed to create task.")

from app.services.google_svc import GoogleService
from datetime import datetime

# ... (rest of imports)

async def handle_calendar(message: types.Message, intent, status_msg):
    user_id = message.from_user.id
    google = GoogleService(user_id)
    
    # 1. Authenticate
    if not await google.authenticate():
        login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login") # Hack for quick link
        await status_msg.edit_text(
            f"⚠️ **Authorization Required**\nI can't add events yet. Please login first:\n[Connect Google Calendar]({login_url})",
            parse_mode="Markdown"
        )
        return

    # 2. Parse Data
    event_data = intent.calendar
    try:
        # Expected format from LLM now: YYYY-MM-DD HH:MM:SS
        start_dt = datetime.strptime(event_data.start_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Fallback if LLM failed strict format, try ISO or fuzzy
        logger.warning(f"Date parse failed for {event_data.start_time}, trying flexible")
        try:
             start_dt = datetime.fromisoformat(event_data.start_time)
        except:
             await status_msg.edit_text(f"❌ Failed to understand date: {event_data.start_time}")
             return

    # 3. Create Event
    link = await google.create_calendar_event(event_data.summary, start_dt)
    
    if link:
        await status_msg.edit_text(
            f"📅 **Event Created!**\n"
            f"📝 {event_data.summary}\n"
            f"🕒 {start_dt.strftime('%d/%m %H:%M')}\n"
            f"🔗 [View in Calendar]({link})",
            parse_mode="Markdown"
        )
    else:
        await status_msg.edit_text("❌ Failed to create event in Google Calendar.")

from app.services.archive_service import save_note

async def handle_note(message: types.Message, intent, status_msg):
    user_id = message.from_user.id
    note_data = intent.note
    
    saved_note = await save_note(user_id, note_data.content, note_data.tags)
    
    if saved_note:
        tags_str = " ".join([f"#{t}" for t in note_data.tags])
        await status_msg.edit_text(
            f"🧠 **Note Archived**\n"
            f"📝 {note_data.content}\n"
            f"🏷 {tags_str}",
            parse_mode="Markdown"
        )
    else:
        await status_msg.edit_text("❌ Failed to save note.")

from app.services.query_service import QueryService

async def handle_query(message: types.Message, intent, status_msg):
    # If it's just a greeting, handle it simply
    if intent.query.query.lower() in ["general greeting", "hello", "hi"]:
        await status_msg.edit_text("👋 Hey! What can I help you with today?")
        return

    qs = QueryService(message.from_user.id)
    answer = await qs.answer_query(intent.query.query, intent.query.context_needed)
    
    await status_msg.edit_text(answer, parse_mode="Markdown")

