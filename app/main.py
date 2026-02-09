import httpx
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse
from aiogram import types
from app.core.config import settings
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.bot.middleware import IDGuardMiddleware
from app.bot.routers import tasks, auth, google_routes
from app.bot.loader import bot, dp

# Register Middleware
dp.update.outer_middleware(IDGuardMiddleware())

# Register Routers
dp.include_router(google_routes.router)
dp.include_router(tasks.router)


app = FastAPI(title=settings.PROJECT_NAME)
app.include_router(auth.router)

from app.bot.routers import cron
app.include_router(cron.router)


async def _hand_off_to_processor(update_data: dict):
    """Send '...' placeholder, then fire off processing to a separate function."""
    try:
        # Extract chat info from the update
        msg = update_data.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text")

        if not chat_id or not text:
            # Non-text update (sticker, photo, etc.) — process directly
            update = types.Update(**update_data)
            await dp.feed_update(bot, update)
            return

        # Send "..." placeholder immediately
        status = await bot.send_message(chat_id=chat_id, text="...")
        status_msg_id = status.message_id

        # Fire off to /api/process — a NEW Vercel function with its own 10s
        payload = {
            "update_data": update_data,
            "status_msg_id": status_msg_id,
        }
        async with httpx.AsyncClient(timeout=2) as client:
            try:
                await client.post(
                    f"{settings.WEBHOOK_URL.replace('/webhook', '')}/api/process",
                    json=payload,
                    headers={"X-Internal-Secret": settings.M_WEBHOOK_SECRET},
                )
            except httpx.TimeoutException:
                pass  # Expected — we don't wait for the response

    except Exception as e:
        logger.error(f"Hand-off error: {e}")
        # Fallback: process directly in this function
        try:
            update = types.Update(**update_data)
            await dp.feed_update(bot, update)
        except Exception as e2:
            logger.error(f"Fallback processing error: {e2}")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_token != settings.M_WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret")
            return JSONResponse({"status": "unauthorized"})

        update_data = await request.json()
        return JSONResponse(
            {"status": "ok"},
            background=BackgroundTask(_hand_off_to_processor, update_data),
        )
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return JSONResponse({"status": "error"})


@app.post("/api/process")
async def process_message(request: Request):
    """Heavy processing endpoint — gets its own fresh 10s function timeout."""
    try:
        # Auth check
        secret = request.headers.get("X-Internal-Secret")
        if secret != settings.M_WEBHOOK_SECRET:
            return JSONResponse({"status": "unauthorized"}, status_code=401)

        data = await request.json()
        update_data = data["update_data"]
        status_msg_id = data["status_msg_id"]

        msg = update_data.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        text = msg.get("text", "")

        if not chat_id or not text:
            return JSONResponse({"status": "skipped"})

        # Import here to avoid circular imports at module level
        from app.services.router_service import route_intent
        from app.services.memory_service import log_interaction, get_relevant_insights
        from app.services.query_service import QueryService
        from app.services.url_service import extract_urls, fetch_url_content, summarize_and_tag
        from app.services.archive_service import save_url_knowledge
        from app.services.task_service import create_task, complete_task, delete_task
        from app.services.google_svc import GoogleService
        from app.services.archive_service import save_note
        from datetime import datetime

        async def edit_status(new_text: str):
            try:
                await bot.edit_message_text(
                    text=new_text, chat_id=chat_id, message_id=status_msg_id
                )
            except Exception as e:
                logger.error(f"Failed to edit message: {e}")

        # URL interception
        urls = extract_urls(text)
        if urls:
            try:
                url = urls[0]
                fetched = await fetch_url_content(url)
                if fetched["error"] and not fetched["content"]:
                    await edit_status(f"Couldn't access the link, saving URL only: {url}")
                    await save_url_knowledge(
                        user_id=user_id, url=url, title=url, content="",
                        summary=f"Saved link: {url}", tags=[], key_points=[],
                    )
                else:
                    result = await summarize_and_tag(url, fetched["title"], fetched["content"])
                    await save_url_knowledge(
                        user_id=user_id, url=url, title=fetched["title"],
                        content=fetched["content"], summary=result["summary"],
                        tags=result["tags"], key_points=result["key_points"],
                    )
                    tags_str = " ".join([f"#{t}" for t in result["tags"]]) if result["tags"] else ""
                    kp_str = "\n" + "\n".join([f"- {kp}" for kp in result["key_points"]]) if result["key_points"] else ""
                    await edit_status(f"Saved: {fetched['title']}\n\n{result['summary']}{kp_str}\n\n{tags_str}")
                await log_interaction(user_id=user_id, user_message=text, bot_response="URL saved", action_type="note", intent_summary="URL save")
            except Exception as e:
                logger.error(f"URL processing error: {e}")
                await edit_status("Error processing the link.")
            return JSONResponse({"status": "ok"})

        # Route intent
        intent = await route_intent(text)
        action_type = intent.classification.action_type

        memory_context = await get_relevant_insights(user_id=user_id, action_type=action_type, query_text=text)

        bot_response = None

        if action_type == "task" and intent.task:
            action = getattr(intent.task, 'action', 'create')
            if action == "complete":
                result = await complete_task(user_id, intent.task.title)
                bot_response = f"Done: {result['title']}" if result else f"Couldn't find a matching task for \"{intent.task.title}\""
            elif action == "delete":
                result = await delete_task(user_id, intent.task.title)
                bot_response = f"Deleted: {result['title']}" if result else f"Couldn't find a matching task for \"{intent.task.title}\""
            else:
                task_data = intent.task.model_dump()
                task = await create_task(user_id, task_data)
                if task:
                    due_str = f"\nDue: {task.get('due_at')}" if task.get('due_at') else ""
                    bot_response = f"Task created: {task['title']}{due_str}"
                else:
                    bot_response = "Failed to create task."

        elif action_type == "calendar" and intent.calendar:
            google = GoogleService(user_id)
            if not await google.authenticate():
                login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
                bot_response = f"Need to connect Google first:\n{login_url}"
            else:
                event_data = intent.calendar
                try:
                    start_dt = datetime.strptime(event_data.start_time, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        start_dt = datetime.fromisoformat(event_data.start_time)
                    except Exception:
                        start_dt = None
                if start_dt:
                    link = await google.create_calendar_event(event_data.summary, start_dt)
                    if link:
                        bot_response = f"Scheduled: {event_data.summary}\n{start_dt.strftime('%d/%m %H:%M')}\n{link}"
                    else:
                        bot_response = "Failed to create calendar event."
                else:
                    bot_response = f"Couldn't parse the date: {event_data.start_time}"

        elif action_type == "note" and intent.note:
            saved = await save_note(user_id, intent.note.content, intent.note.tags)
            if saved:
                tags_str = " ".join([f"#{t}" for t in intent.note.tags])
                bot_response = f"Saved: {intent.note.content}\n{tags_str}"
            else:
                bot_response = "Failed to save note."

        elif action_type == "query":
            qs = QueryService(user_id)
            query_text = intent.query.query if intent.query else text
            target_date = intent.query.target_date if intent.query else None
            context_needed = intent.query.context_needed if intent.query else []
            bot_response = await qs.answer_query(query_text, context_needed, target_date, memory_context)

        else:
            bot_response = "Not sure what to do with that."

        if bot_response:
            await edit_status(bot_response)
            await log_interaction(
                user_id=user_id, user_message=text, bot_response=bot_response,
                action_type=action_type, intent_summary=intent.classification.summary,
            )

        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Process error: {e}")
        # Try to update the status message with error
        try:
            data = await request.json()
            chat_id = data["update_data"]["message"]["chat"]["id"]
            status_msg_id = data["status_msg_id"]
            await bot.edit_message_text(
                text="Something went wrong. Try again.",
                chat_id=chat_id, message_id=status_msg_id,
            )
        except Exception:
            pass
        return JSONResponse({"status": "error"})


@app.get("/")
async def root():
    return {"message": "Telegram Command Center is running"}

@app.get("/setup-webhook")
async def setup_webhook():
    """Manual one-time webhook setup. Call once after deploy, not on every boot."""
    try:
        await bot.set_webhook(
            url=settings.WEBHOOK_URL,
            secret_token=settings.M_WEBHOOK_SECRET,
            drop_pending_updates=False,
        )
        return {"status": "ok", "webhook_url": settings.WEBHOOK_URL}
    except Exception as e:
        return {"status": "error", "message": str(e)}
