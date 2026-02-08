from aiogram import Router, F, types
from aiogram.filters import Command
from app.services.router_service import route_intent
from app.services.task_service import create_task
from app.services.memory_service import log_interaction, get_relevant_insights
from app.services.url_service import extract_urls, fetch_url_content, summarize_and_tag
from app.services.archive_service import save_url_knowledge
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
router = Router()


async def safe_edit(status_msg, text: str):
    """Edit message — plain text to avoid Markdown parsing issues."""
    try:
        await status_msg.edit_text(text)
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("יו, מה קורה? אני פה. ספר לי מה צריך.")


@router.message(F.text)
async def handle_router_message(message: types.Message):
    if not message.text:
        return

    # URL interception — skip router if message contains URLs
    urls = extract_urls(message.text)
    if urls:
        status_msg = await message.answer("🔗 מעבד קישור...")
        bot_response = await handle_url_save(message, urls, status_msg)
        if bot_response:
            await log_interaction(
                user_id=message.from_user.id,
                user_message=message.text,
                bot_response=bot_response,
                action_type="note",
                intent_summary="URL save",
            )
        return

    status_msg = await message.answer("🧠 חושב...")

    try:
        # 1. Route Intent
        intent = await route_intent(message.text)
        action_type = intent.classification.action_type

        # 2. Get memory context
        memory_context = await get_relevant_insights(
            user_id=message.from_user.id,
            action_type=action_type,
            query_text=message.text,
        )

        # 3. Dispatch and capture response
        bot_response = None

        if action_type == "task":
            bot_response = await handle_task(message, intent, status_msg)
        elif action_type == "calendar":
            bot_response = await handle_calendar(message, intent, status_msg)
        elif action_type == "note":
            bot_response = await handle_note(message, intent, status_msg)
        elif action_type == "query":
            bot_response = await handle_query(message, intent, status_msg, memory_context)
        else:
            bot_response = "🤷 לא בטוח מה לעשות עם זה."
            await safe_edit(status_msg, bot_response)

        # 4. Log interaction (fire-and-forget, never blocks)
        if bot_response:
            await log_interaction(
                user_id=message.from_user.id,
                user_message=message.text,
                bot_response=bot_response,
                action_type=action_type,
                intent_summary=intent.classification.summary,
            )

    except Exception as e:
        logger.error(f"Handler Error: {e}")
        await safe_edit(status_msg, "❌ שגיאה בעיבוד הבקשה.")


async def handle_task(message: types.Message, intent, status_msg) -> str | None:
    if not intent.task:
        text = "❌ זוהה כמשימה אבל חסרים פרטים."
        await safe_edit(status_msg, text)
        return text

    user_id = message.from_user.id
    task_data = intent.task.model_dump()

    task = await create_task(user_id, task_data)

    if task:
        due_str = f"\nעד: {task.get('due_at')}" if task.get('due_at') else ""
        text = (
            f"נוצרה משימה: {task['title']}"
            f"{due_str}"
        )
        await safe_edit(status_msg, text)
        return text
    else:
        text = "❌ נכשל ביצירת המשימה."
        await safe_edit(status_msg, text)
        return text


from app.services.google_svc import GoogleService
from datetime import datetime


async def handle_calendar(message: types.Message, intent, status_msg) -> str | None:
    user_id = message.from_user.id
    google = GoogleService(user_id)

    # 1. Authenticate
    if not await google.authenticate():
        login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
        text = f"⚠️ *נדרש חיבור לגוגל*\nאנא התחבר קודם:\n[התחבר עם Google]({login_url})"
        await safe_edit(status_msg, text)
        return text

    # 2. Parse Data
    event_data = intent.calendar
    try:
        start_dt = datetime.strptime(event_data.start_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning(f"Date parse failed for {event_data.start_time}, trying flexible")
        try:
             start_dt = datetime.fromisoformat(event_data.start_time)
        except:
             text = f"❌ לא הצלחתי להבין את התאריך: {event_data.start_time}"
             await safe_edit(status_msg, text)
             return text

    # 3. Create Event
    link = await google.create_calendar_event(event_data.summary, start_dt)

    if link:
        text = (
            f"נקבע: {event_data.summary}\n"
            f"{start_dt.strftime('%d/%m %H:%M')}\n"
            f"{link}"
        )
        await safe_edit(status_msg, text)
        return text
    else:
        text = "❌ נכשל ביצירת אירוע ביומן Google."
        await safe_edit(status_msg, text)
        return text


from app.services.archive_service import save_note


async def handle_note(message: types.Message, intent, status_msg) -> str | None:
    user_id = message.from_user.id
    note_data = intent.note

    saved_note = await save_note(user_id, note_data.content, note_data.tags)

    if saved_note:
        tags_str = " ".join([f"#{t}" for t in note_data.tags])
        text = f"נשמר: {note_data.content}\n{tags_str}"
        await safe_edit(status_msg, text)
        return text
    else:
        text = "❌ נכשל בשמירת ההערה."
        await safe_edit(status_msg, text)
        return text


from app.services.query_service import QueryService


async def handle_query(message: types.Message, intent, status_msg, memory_context: str = "") -> str | None:
    qs = QueryService(message.from_user.id)
    query_text = intent.query.query if intent.query else message.text
    target_date = intent.query.target_date if intent.query else None
    context_needed = intent.query.context_needed if intent.query else []
    answer = await qs.answer_query(query_text, context_needed, target_date, memory_context)

    await safe_edit(status_msg, answer)
    return answer


async def handle_url_save(message: types.Message, urls: list[str], status_msg) -> str | None:
    """Fetch, summarize, and save URL content to the knowledge archive."""
    try:
        url = urls[0]  # Process first URL
        fetched = await fetch_url_content(url)

        if fetched["error"] and not fetched["content"]:
            text = f"⚠️ לא הצלחתי לגשת לקישור: {url}\nשומר את הקישור בלבד."
            await save_url_knowledge(
                user_id=message.from_user.id,
                url=url, title=url, content="",
                summary=f"קישור שנשמר: {url}", tags=[], key_points=[],
            )
            await safe_edit(status_msg, text)
            return text

        result = await summarize_and_tag(url, fetched["title"], fetched["content"])

        saved = await save_url_knowledge(
            user_id=message.from_user.id,
            url=url,
            title=fetched["title"],
            content=fetched["content"],
            summary=result["summary"],
            tags=result["tags"],
            key_points=result["key_points"],
        )

        tags_str = " ".join([f"#{t}" for t in result["tags"]]) if result["tags"] else ""
        kp_str = ""
        if result["key_points"]:
            kp_str = "\n" + "\n".join([f"- {kp}" for kp in result["key_points"]])

        text = (
            f"שמרתי: {fetched['title']}\n\n"
            f"{result['summary']}"
            f"{kp_str}\n\n"
            f"{tags_str}"
        )
        await safe_edit(status_msg, text)
        return text

    except Exception as e:
        logger.error(f"URL save error: {e}")
        text = "❌ שגיאה בעיבוד הקישור."
        await safe_edit(status_msg, text)
        return text
