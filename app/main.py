import asyncio
import time
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
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

# --- Confirmation flow (persisted to Supabase) ---
_CONFIRM_TTL = 120  # 2 minutes


def _save_confirmation(user_id: int, action_name: str, action_data: dict):
    """Save a pending confirmation to Supabase."""
    try:
        supabase.table("pending_confirmations").upsert({
            "user_id": user_id,
            "action_name": action_name,
            "action_data": action_data,
            "created_at": datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        logger.warning(f"Failed to save confirmation to DB: {e}")


def _get_confirmation(user_id: int) -> tuple[str, dict] | None:
    """Retrieve and delete a pending confirmation from Supabase."""
    try:
        resp = (
            supabase.table("pending_confirmations")
            .select("action_name, action_data, created_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        # Check TTL
        created = datetime.fromisoformat(row["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=ZoneInfo("Asia/Jerusalem"))
        age = (datetime.now(ZoneInfo("Asia/Jerusalem")) - created).total_seconds()
        # Delete regardless (consumed or expired)
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
        if age > _CONFIRM_TTL:
            return None
        return (row["action_name"], row["action_data"])
    except Exception as e:
        logger.warning(f"Failed to get confirmation from DB, falling back: {e}")
        return None


def _cancel_confirmation(user_id: int):
    """Cancel any pending confirmation."""
    try:
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
    except Exception:
        pass


async def _transcribe_voice(file_id: str) -> str | None:
    """Download Telegram voice message and transcribe via Gemini."""
    try:
        from google import genai
        from google.genai import types as genai_types
        import io

        file_info = await bot.get_file(file_id)
        file_bytes = io.BytesIO()
        await bot.download_file(file_info.file_path, file_bytes)
        audio_data = file_bytes.getvalue()

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL_FALLBACK,  # 2.0 Flash handles audio
                contents=[
                    genai_types.Content(parts=[
                        genai_types.Part.from_bytes(data=audio_data, mime_type="audio/ogg"),
                        genai_types.Part.from_text("Transcribe this audio message to text. Return ONLY the transcribed text, nothing else. The language is likely Hebrew."),
                    ]),
                ],
            ),
            timeout=15,
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        return None


async def _process_update(update_data: dict):
    """Process a Telegram update directly in background (no HTTP self-call needed on Render)."""
    chat_id = None
    status_msg_id = None

    try:
        msg = update_data.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        text = msg.get("text")
        update_id = update_data.get("update_id")

        # --- Voice message transcription ---
        voice = msg.get("voice") or msg.get("audio")
        if voice and not text:
            if user_id != settings.TELEGRAM_USER_ID:
                return
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            status = await bot.send_message(chat_id=chat_id, text="🎤 מתמלל...")
            transcribed = await _transcribe_voice(voice["file_id"])
            if transcribed:
                text = transcribed
                try:
                    await bot.edit_message_text(
                        text=f"🎤 \"{transcribed}\"\n\n⏳",
                        chat_id=chat_id, message_id=status.message_id,
                    )
                except Exception:
                    pass
            else:
                await bot.edit_message_text(
                    text="לא הצלחתי לתמלל את ההודעה הקולית.",
                    chat_id=chat_id, message_id=status.message_id,
                )
                return

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

        # --- Webhook deduplication ---
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
                    return
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

        _MODEL_DISPLAY = {
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-2.0-flash": "Gemini 2.0 Flash",
            "moonshotai/kimi-k2-instruct-0905": "Kimi K2",
        }

        async def edit_status(new_text: str):
            try:
                from app.core.llm import last_model_used
                model = last_model_used.get("")
                if model:
                    short = _MODEL_DISPLAY.get(model, model)
                    new_text += f"\n\n({short})"
            except Exception:
                pass
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

            # --- Confirmation check (persisted in Supabase) ---
            text_lower = text.strip().lower()
            if text_lower in ("כן", "yes", "confirm", "אישור"):
                pending = _get_confirmation(user_id)
                if pending:
                    action_name, action_data = pending
                    if action_name == "complete_all":
                        count = await complete_all_tasks(user_id)
                        bot_response = f"סיימתי! סימנתי {count} משימות כבוצעו ✅" if count > 0 else "אין משימות פתוחות."
                    elif action_name == "delete":
                        result = await delete_task(user_id, action_data["title"])
                        bot_response = f"נמחק: {result['title']} 🗑" if result else f"לא מצאתי את \"{action_data['title']}\"."
                    elif action_name == "create_task":
                        task = await create_task(user_id, action_data)
                        bot_response = f"נוסף: {task['title']}" if task else "משהו השתבש בשמירת המשימה."
                    elif action_name == "schedule_task":
                        # User selected a slot number (1, 2, 3)
                        slot_idx = None
                        for ch in text.strip():
                            if ch.isdigit() and 1 <= int(ch) <= 3:
                                slot_idx = int(ch) - 1
                                break
                        slots = action_data.get("slots", [])
                        if slot_idx is not None and slot_idx < len(slots):
                            slot = slots[slot_idx]
                            google_svc = GoogleService(user_id)
                            if await google_svc.authenticate():
                                start_dt = datetime.fromisoformat(slot["start"])
                                end_dt = datetime.fromisoformat(slot["end"])
                                link = await google_svc.create_calendar_event(
                                    action_data["task_title"], start_dt, end_dt=end_dt,
                                )
                                bot_response = f"נקבע: {action_data['task_title']}\n{slot['day']} {slot['time']}\n{link}" if link else "לא הצלחתי ליצור אירוע."
                            else:
                                bot_response = "שגיאה בחיבור ל-Google."
                        else:
                            bot_response = "בוטל."
                    else:
                        bot_response = "בוצע."
                    await edit_status(bot_response)
                    await log_interaction(
                        user_id=user_id, user_message=text, bot_response=bot_response,
                        action_type="task", intent_summary=f"Confirmed {action_name}",
                        telegram_update_id=update_id,
                    )
                    return

            # Cancel confirmation on any other message
            _cancel_confirmation(user_id)

            # URL interception
            urls = extract_urls(text)
            if urls:
                try:
                    url = urls[0]
                    fetched = await fetch_url_content(url)
                    if fetched["error"] and not fetched["content"]:
                        await edit_status(f"לא הצלחתי לגשת ללינק, שומר את ה-URL: {url}")
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
                        await edit_status(f"נשמר: {fetched['title']}\n\n{result['summary']}{kp_str}\n\n{tags_str}")
                    await log_interaction(
                        user_id=user_id, user_message=text, bot_response="URL saved",
                        action_type="note", intent_summary="URL save",
                        telegram_update_id=update_id,
                    )
                except Exception as e:
                    logger.error(f"URL processing error: {e}")
                    await edit_status("שגיאה בעיבוד הלינק.")
                return

            # --- Parallel intent + memory (Batch 3) ---
            intent_result, memory_result = await asyncio.gather(
                route_intent(text, user_id=user_id),
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

            # --- Ambiguous input suggestions ---
            if intent.classification.confidence < 0.55 and action_type not in ("chat",):
                suggestions = []
                if intent.task:
                    suggestions.append(f"1. משימה: \"{intent.task.title}\"")
                if intent.query:
                    suggestions.append(f"{'2' if suggestions else '1'}. שאלה: \"{intent.query.query[:50]}\"")
                suggestions.append(f"{len(suggestions)+1}. שיחה חופשית")
                bot_response = f"לא בטוח מה התכוונת. אפשרויות:\n" + "\n".join(suggestions) + "\n\nשלח את המספר או נסח מחדש."
                _save_confirmation(user_id, "disambiguate", {
                    "original_text": text,
                    "options": {
                        "1": action_type,
                        "2": "query" if len(suggestions) > 2 else "chat",
                        "3": "chat",
                    },
                })
                await edit_status(bot_response)
                await log_interaction(
                    user_id=user_id, user_message=text, bot_response=bot_response,
                    action_type="system", intent_summary="Ambiguous input",
                    telegram_update_id=update_id,
                )
                return

            if action_type == "task" and intent.task:
                action = getattr(intent.task, 'action', 'create')

                if action == "complete":
                    result = await complete_task(user_id, intent.task.title)
                    if result:
                        bot_response = f"בוצע: {result['title']} ✅"
                    else:
                        # Smart task match feedback (Batch 4)
                        pending = await get_pending_tasks(user_id, limit=20)
                        if pending:
                            task_list = "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(pending)])
                            bot_response = f"לא מצאתי \"{intent.task.title}\" במשימות הפתוחות.\n\nהמשימות שלך:\n{task_list}"
                        else:
                            bot_response = f"לא מצאתי \"{intent.task.title}\" — אין משימות פתוחות בכלל."

                elif action == "complete_all":
                    # Confirmation flow (Batch 5)
                    pending = await get_pending_tasks(user_id, limit=50)
                    count = len(pending) if pending else 0
                    if count == 0:
                        bot_response = "אין משימות פתוחות."
                    else:
                        _save_confirmation(user_id, "complete_all", {})
                        task_list = "\n".join([f"  - {t['title']}" for t in pending[:10]])
                        extra = f"\n  ... ועוד {count - 10}" if count > 10 else ""
                        bot_response = f"עומד לסמן {count} משימות כבוצעו:\n{task_list}{extra}\n\nשלח 'כן' לאישור."

                elif action == "delete":
                    _save_confirmation(user_id, "delete", {"title": intent.task.title})
                    bot_response = f"עומד למחוק: \"{intent.task.title}\"\nשלח 'כן' לאישור."

                elif action == "schedule":
                    # Find the task and suggest free calendar slots
                    existing = await get_pending_tasks(user_id, limit=50)
                    from app.services.task_service import _match_task
                    match = _match_task(existing, intent.task.title) if existing else None
                    if not match:
                        bot_response = f"לא מצאתי משימה בשם \"{intent.task.title}\" לתזמון."
                    else:
                        google = GoogleService(user_id)
                        if not await google.authenticate():
                            login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
                            bot_response = f"צריך לחבר Google קודם:\n{login_url}"
                        else:
                            # Parse effort to minutes
                            effort_map = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}
                            effort_str = match.get("effort", "1h")
                            duration = effort_map.get(effort_str, 60)
                            slots = await google.find_free_slots(duration_minutes=duration, days_ahead=3, max_slots=3)
                            if slots:
                                lines = [f"חלונות פנויים ל-\"{match['title']}\" ({effort_str}):"]
                                for i, s in enumerate(slots, 1):
                                    lines.append(f"{i}. {s['day']} {s['time']}")
                                lines.append("\nשלח את המספר לקביעה, או אמור 'לא' לביטול.")
                                bot_response = "\n".join(lines)
                                _save_confirmation(user_id, "schedule_task", {
                                    "task_title": match["title"],
                                    "slots": slots,
                                })
                            else:
                                bot_response = "לא מצאתי חלונות פנויים ב-3 הימים הקרובים."

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
                            changes.append(f"שם חדש: \"{updates['title']}\"")
                        if "due_date" in updates:
                            changes.append(f"נדחה ל-{updates['due_date']}")
                        if "priority" in updates:
                            changes.append(f"עדיפות: {updates['priority']}")
                        bot_response = f"עודכן: {result['title']}\n" + ", ".join(changes)
                    else:
                        pending = await get_pending_tasks(user_id, limit=20)
                        if pending:
                            task_list = "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(pending)])
                            bot_response = f"לא מצאתי \"{intent.task.title}\".\n\nהמשימות שלך:\n{task_list}"
                        else:
                            bot_response = f"לא מצאתי \"{intent.task.title}\" — אין משימות פתוחות."

                else:
                    # --- Duplicate detection ---
                    task_data = intent.task.model_dump()
                    existing = await get_pending_tasks(user_id, limit=50)
                    if existing:
                        from app.services.task_service import _match_task
                        dup = _match_task(existing, task_data.get("title", ""))
                        if dup:
                            bot_response = f"כבר יש משימה דומה: \"{dup['title']}\"\nרוצה שאוסיף בכל זאת?"
                            _save_confirmation(user_id, "create_task", task_data)
                            await edit_status(bot_response)
                            await log_interaction(
                                user_id=user_id, user_message=text, bot_response=bot_response,
                                action_type="task", intent_summary="Duplicate detected",
                                telegram_update_id=update_id,
                            )
                            return
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
                        recur_str = f"\n🔄 חוזר {recurrence}" if recurrence else ""
                        effort = task_data.get('effort')
                        effort_str = f"\n⏱ {effort}" if effort else ""
                        bot_response = f"נוסף: {task['title']}{due_str}{recur_str}{effort_str}"
                    else:
                        bot_response = "משהו השתבש בשמירת המשימה. נסה שוב?"

            elif action_type == "calendar" and intent.calendar:
                google = GoogleService(user_id)
                if not await google.authenticate():
                    login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
                    bot_response = f"צריך לחבר Google קודם:\n{login_url}"
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
                            bot_response = f"נקבע: {event_data.summary}\n{start_dt.strftime('%d/%m %H:%M')}{duration_str}{loc_str}\n{link}"
                        else:
                            bot_response = "לא הצלחתי ליצור אירוע ביומן."
                    else:
                        bot_response = f"לא הצלחתי לפרסר את התאריך: {event_data.start_time}"

            elif action_type == "note" and intent.note:
                saved = await save_note(user_id, intent.note.content, intent.note.tags)
                if saved:
                    tags_str = " ".join([f"#{t}" for t in intent.note.tags])
                    bot_response = f"נשמר: {intent.note.content}\n{tags_str}"
                else:
                    bot_response = "לא הצלחתי לשמור."

            elif action_type == "chat":
                # Lightweight chat — no context fetching, just LLM with identity
                from app.core.llm import llm_call as _llm
                from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
                chat_resp = await _llm(
                    messages=[
                        {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.8,
                    timeout=10,
                )
                bot_response = chat_resp.choices[0].message.content if chat_resp else "הנה אני, מה קורה?"

            elif action_type == "query":
                qs = QueryService(user_id)
                query_text = intent.query.query if intent.query else text
                target_date = intent.query.target_date if intent.query else None
                context_needed = intent.query.context_needed if intent.query else []
                archive_since = getattr(intent.query, 'archive_since', None) if intent.query else None
                bot_response = await qs.answer_query(query_text, context_needed, target_date, memory_context, archive_since=archive_since)

            else:
                bot_response = "לא בטוח מה לעשות עם זה."

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
                    text="לקח יותר מדי זמן, נסה שוב.",
                    chat_id=chat_id, message_id=status_msg_id,
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Process error: {e}")
        # Try to update the status message with error
        if chat_id and status_msg_id:
            try:
                await bot.edit_message_text(
                    text="משהו השתבש. נסה שוב.",
                    chat_id=chat_id, message_id=status_msg_id,
                )
            except Exception:
                pass


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates and process in background."""
    data = await request.json()
    return JSONResponse({"ok": True}, background=BackgroundTask(_process_update, data))


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
