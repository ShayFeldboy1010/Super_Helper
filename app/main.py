import asyncio
import time
import httpx
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse
from aiogram import types
from app.core.config import settings
from app.core.database import supabase
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

# --- Confirmation flow (Batch 5) ---
# Stores pending destructive actions: user_id -> (action_name, data_dict, timestamp)
_pending_confirmations: dict[int, tuple[str, dict, float]] = {}
_CONFIRM_TTL = 120  # 2 minutes


async def _hand_off_to_processor(update_data: dict):
    """Send placeholder, then process directly (no Vercel timeout workaround needed on Render)."""
    try:
        msg = update_data.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        text = msg.get("text")

        if not chat_id or not text:
            update = types.Update(**update_data)
            await dp.feed_update(bot, update)
            return

        if user_id != settings.TELEGRAM_USER_ID:
            logger.warning(f"Unauthorized user {user_id}")
            return

        await bot.send_chat_action(chat_id=chat_id, action="typing")
        status = await bot.send_message(chat_id=chat_id, text="\u23f3")
        status_msg_id = status.message_id

        # Process directly — Render has no 10s function timeout
        async with httpx.AsyncClient(timeout=2) as client:
            try:
                await client.post(
                    f"{settings.WEBHOOK_URL.rsplit('/webhook', 1)[0]}/api/process",
                    json={"update_data": update_data, "status_msg_id": status_msg_id},
                    headers={"X-Internal-Secret": settings.M_WEBHOOK_SECRET},
                )
            except (httpx.TimeoutException, httpx.ConnectError):
                pass  # Fire-and-forget

    except Exception as e:
        logger.error(f"Hand-off error: {e}")
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
    # Parse request data first so outer except can use chat_id / status_msg_id
    data = None
    chat_id = None
    status_msg_id = None

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
        update_id = update_data.get("update_id")

        if not chat_id or not text:
            return JSONResponse({"status": "skipped"})

        # --- Webhook deduplication (Batch 2) ---
        if update_id:
            try:
                dup = (
                    supabase.table("interaction_log")
                    .select("id")
                    .eq("telegram_update_id", update_id)
                    .limit(1)
                    .execute()
                )
                if dup.data:
                    logger.info(f"Duplicate update_id {update_id}, skipping")
                    return JSONResponse({"status": "duplicate"})
            except Exception as e:
                logger.warning(f"Dedup check failed (proceeding): {e}")

        # Import here to avoid circular imports at module level
        from app.services.router_service import route_intent
        from app.services.memory_service import log_interaction, get_relevant_insights
        from app.services.query_service import QueryService
        from app.services.url_service import extract_urls, fetch_url_content, summarize_and_tag
        from app.services.archive_service import save_url_knowledge
        from app.services.task_service import (
            create_task, complete_task, delete_task, complete_all_tasks,
            get_pending_tasks, edit_task,
        )
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

        # --- Core processing wrapped with timeout (Batch 2) ---
        async def _process_core():
            nonlocal chat_id, status_msg_id

            # Typing indicator (Batch 4)
            await bot.send_chat_action(chat_id=chat_id, action="typing")

            # --- Confirmation check (Batch 5) ---
            text_lower = text.strip().lower()
            if text_lower in ("כן", "yes", "confirm", "אישור"):
                pending = _pending_confirmations.pop(user_id, None)
                if pending and (time.time() - pending[2]) < _CONFIRM_TTL:
                    action_name, action_data, _ = pending
                    if action_name == "complete_all":
                        count = await complete_all_tasks(user_id)
                        bot_response = f"All done! Marked {count} tasks as completed ✅" if count > 0 else "No open tasks to complete."
                    elif action_name == "delete":
                        result = await delete_task(user_id, action_data["title"])
                        bot_response = f"Removed: {result['title']} 🗑" if result else f"Can't find \"{action_data['title']}\" anymore."
                    else:
                        bot_response = "Done."
                    await edit_status(bot_response)
                    await log_interaction(
                        user_id=user_id, user_message=text, bot_response=bot_response,
                        action_type="task", intent_summary=f"Confirmed {action_name}",
                        telegram_update_id=update_id,
                    )
                    return
                elif text_lower in ("כן", "yes", "confirm", "אישור"):
                    await edit_status("Nothing pending to confirm.")
                    return

            # Cancel confirmation on any other message
            if text_lower not in ("כן", "yes", "confirm", "אישור"):
                if user_id in _pending_confirmations:
                    _pending_confirmations.pop(user_id, None)

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
                    await log_interaction(
                        user_id=user_id, user_message=text, bot_response="URL saved",
                        action_type="note", intent_summary="URL save",
                        telegram_update_id=update_id,
                    )
                except Exception as e:
                    logger.error(f"URL processing error: {e}")
                    await edit_status("Error processing the link.")
                return

            # --- Parallel intent + memory (Batch 3) ---
            intent_result, memory_result = await asyncio.gather(
                route_intent(text),
                get_relevant_insights(user_id=user_id, action_type="query", query_text=text),
                return_exceptions=True,
            )

            # Handle exceptions
            if isinstance(intent_result, Exception):
                logger.error(f"Intent routing failed: {intent_result}")
                from app.models.router_models import ActionClassification, QueryPayload
                intent_result = __import__('app.models.router_models', fromlist=['RouterResponse']).RouterResponse(
                    classification=ActionClassification(action_type="query", confidence=0.5, summary="Fallback"),
                    query=QueryPayload(query=text, context_needed=[]),
                )
            if isinstance(memory_result, Exception):
                logger.error(f"Memory fetch failed: {memory_result}")
                memory_result = ""

            intent = intent_result
            memory_context = memory_result
            action_type = intent.classification.action_type

            bot_response = None

            if action_type == "task" and intent.task:
                action = getattr(intent.task, 'action', 'create')

                if action == "complete":
                    result = await complete_task(user_id, intent.task.title)
                    if result:
                        bot_response = f"Done, marked as completed: {result['title']} ✅"
                    else:
                        # Smart task match feedback (Batch 4)
                        pending = await get_pending_tasks(user_id, limit=20)
                        if pending:
                            task_list = "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(pending)])
                            bot_response = f"Can't find \"{intent.task.title}\" in your open tasks.\n\nYour pending tasks:\n{task_list}"
                        else:
                            bot_response = f"Can't find \"{intent.task.title}\" — no open tasks at all."

                elif action == "complete_all":
                    # Confirmation flow (Batch 5)
                    pending = await get_pending_tasks(user_id, limit=50)
                    count = len(pending) if pending else 0
                    if count == 0:
                        bot_response = "No open tasks to complete."
                    else:
                        _pending_confirmations[user_id] = ("complete_all", {}, time.time())
                        task_list = "\n".join([f"  - {t['title']}" for t in pending[:10]])
                        extra = f"\n  ... and {count - 10} more" if count > 10 else ""
                        bot_response = f"About to mark {count} tasks as done:\n{task_list}{extra}\n\nSend 'כן' to confirm."

                elif action == "delete":
                    # Confirmation flow (Batch 5)
                    _pending_confirmations[user_id] = ("delete", {"title": intent.task.title}, time.time())
                    bot_response = f"About to delete: \"{intent.task.title}\"\nSend 'כן' to confirm."

                elif action == "edit":
                    # Task editing (Batch 6)
                    updates = {}
                    if getattr(intent.task, 'new_title', None):
                        updates["title"] = intent.task.new_title
                    if getattr(intent.task, 'new_due_date', None):
                        updates["due_date"] = intent.task.new_due_date
                    if getattr(intent.task, 'new_priority', None) is not None:
                        updates["priority"] = intent.task.new_priority
                    result = await edit_task(user_id, intent.task.title, updates)
                    if result:
                        changes = []
                        if "title" in updates:
                            changes.append(f"renamed to \"{updates['title']}\"")
                        if "due_date" in updates:
                            changes.append(f"rescheduled to {updates['due_date']}")
                        if "priority" in updates:
                            changes.append(f"priority set to {updates['priority']}")
                        bot_response = f"Updated: {result['title']}\n" + ", ".join(changes)
                    else:
                        pending = await get_pending_tasks(user_id, limit=20)
                        if pending:
                            task_list = "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(pending)])
                            bot_response = f"Can't find \"{intent.task.title}\".\n\nYour pending tasks:\n{task_list}"
                        else:
                            bot_response = f"Can't find \"{intent.task.title}\" — no open tasks."

                else:
                    task_data = intent.task.model_dump()
                    task = await create_task(user_id, task_data)
                    if task:
                        due_str = ""
                        if task.get('due_at'):
                            try:
                                dt = datetime.fromisoformat(task['due_at'])
                                due_str = f"\n📅 {dt.strftime('%a %b %d, %H:%M')}"
                            except (ValueError, TypeError):
                                due_str = f"\n📅 {task['due_at']}"
                        recurrence = task_data.get('recurrence')
                        recur_str = f"\n🔄 Repeats {recurrence}" if recurrence else ""
                        bot_response = f"Got it: {task['title']}{due_str}{recur_str}"
                    else:
                        bot_response = "Something went wrong saving the task. Try again?"

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

                    # Parse end_time for duration (Batch 6)
                    end_dt = None
                    if event_data.end_time:
                        try:
                            end_dt = datetime.strptime(event_data.end_time, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            try:
                                end_dt = datetime.fromisoformat(event_data.end_time)
                            except Exception:
                                end_dt = None

                    if start_dt:
                        link = await google.create_calendar_event(
                            event_data.summary, start_dt,
                            end_dt=end_dt,
                            location=event_data.location,
                            description=event_data.description,
                        )
                        if link:
                            duration_str = ""
                            if end_dt:
                                mins = int((end_dt - start_dt).total_seconds() / 60)
                                if mins >= 60:
                                    duration_str = f" ({mins // 60}h{(' ' + str(mins % 60) + 'm') if mins % 60 else ''})"
                                else:
                                    duration_str = f" ({mins}m)"
                            loc_str = f"\n📍 {event_data.location}" if event_data.location else ""
                            bot_response = f"Scheduled: {event_data.summary}\n{start_dt.strftime('%d/%m %H:%M')}{duration_str}{loc_str}\n{link}"
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
                    telegram_update_id=update_id,
                )

        # --- Timeout wrapper (Batch 2) ---
        try:
            await asyncio.wait_for(_process_core(), timeout=55)
        except asyncio.TimeoutError:
            logger.error("Processing timed out after 55s")
            try:
                await bot.edit_message_text(
                    text="That took too long, try again.",
                    chat_id=chat_id, message_id=status_msg_id,
                )
            except Exception:
                pass

        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Process error: {e}")
        # Try to update the status message with error
        if chat_id and status_msg_id:
            try:
                await bot.edit_message_text(
                    text="Something went wrong. Try again.",
                    chat_id=chat_id, message_id=status_msg_id,
                )
            except Exception:
                pass
        return JSONResponse({"status": "error"})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "Telegram Command Center is running"}


# --- Keep-alive self-ping (Render free tier sleeps after 15 min) ---
async def _self_ping():
    """Ping /health every 13 min to prevent Render free-tier sleep."""
    await asyncio.sleep(60)  # Wait for startup
    render_url = settings.RENDER_URL
    if not render_url:
        logger.info("RENDER_URL not set, self-ping disabled")
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"{render_url}/health")
        except Exception:
            pass
        await asyncio.sleep(780)  # 13 minutes


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(_self_ping())


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
