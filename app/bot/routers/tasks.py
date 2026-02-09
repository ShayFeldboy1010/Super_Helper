from aiogram import Router, F, types
from aiogram.filters import Command
from app.services.router_service import route_intent
from app.services.task_service import create_task, complete_task, delete_task
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
    await message.answer("Hey, what's up? I'm here. What do you need?")


@router.message(F.text)
async def handle_router_message(message: types.Message):
    if not message.text:
        return

    # URL interception — skip router if message contains URLs
    urls = extract_urls(message.text)
    if urls:
        status_msg = await message.answer("Processing link...")
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

    status_msg = await message.answer("...")

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
            bot_response = "Not sure what to do with that."
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
        await safe_edit(status_msg, "Something went wrong. Try again.")


async def handle_task(message: types.Message, intent, status_msg) -> str | None:
    if not intent.task:
        text = "Couldn't parse task details."
        await safe_edit(status_msg, text)
        return text

    user_id = message.from_user.id
    action = getattr(intent.task, 'action', 'create')

    if action == "complete":
        result = await complete_task(user_id, intent.task.title)
        if result:
            text = f"Done: {result['title']}"
        else:
            text = f"Couldn't find a matching task for \"{intent.task.title}\""
        await safe_edit(status_msg, text)
        return text

    elif action == "delete":
        result = await delete_task(user_id, intent.task.title)
        if result:
            text = f"Deleted: {result['title']}"
        else:
            text = f"Couldn't find a matching task for \"{intent.task.title}\""
        await safe_edit(status_msg, text)
        return text

    else:  # create
        task_data = intent.task.model_dump()
        task = await create_task(user_id, task_data)

        if task:
            due_str = f"\nDue: {task.get('due_at')}" if task.get('due_at') else ""
            text = f"Task created: {task['title']}{due_str}"
            await safe_edit(status_msg, text)
            return text
        else:
            text = "Failed to create task."
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
        text = f"Need to connect Google first:\n{login_url}"
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
             text = f"Couldn't parse the date: {event_data.start_time}"
             await safe_edit(status_msg, text)
             return text

    # 3. Create Event
    link = await google.create_calendar_event(event_data.summary, start_dt)

    if link:
        text = (
            f"Scheduled: {event_data.summary}\n"
            f"{start_dt.strftime('%d/%m %H:%M')}\n"
            f"{link}"
        )
        await safe_edit(status_msg, text)
        return text
    else:
        text = "Failed to create calendar event."
        await safe_edit(status_msg, text)
        return text


from app.services.archive_service import save_note


async def handle_note(message: types.Message, intent, status_msg) -> str | None:
    user_id = message.from_user.id
    note_data = intent.note

    saved_note = await save_note(user_id, note_data.content, note_data.tags)

    if saved_note:
        tags_str = " ".join([f"#{t}" for t in note_data.tags])
        text = f"Saved: {note_data.content}\n{tags_str}"
        await safe_edit(status_msg, text)
        return text
    else:
        text = "Failed to save note."
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
            text = f"Couldn't access the link, saving URL only: {url}"
            await save_url_knowledge(
                user_id=message.from_user.id,
                url=url, title=url, content="",
                summary=f"Saved link: {url}", tags=[], key_points=[],
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
            f"Saved: {fetched['title']}\n\n"
            f"{result['summary']}"
            f"{kp_str}\n\n"
            f"{tags_str}"
        )
        await safe_edit(status_msg, text)
        return text

    except Exception as e:
        logger.error(f"URL save error: {e}")
        text = "Error processing the link."
        await safe_edit(status_msg, text)
        return text
