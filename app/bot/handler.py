"""Telegram message handler — processes incoming updates and routes to services.

This module contains the core message processing pipeline:
1. Voice transcription (if audio)
2. Webhook deduplication
3. Confirmation flow handling
4. URL interception and summarization
5. Intent classification via LLM router
6. Action dispatch (task, calendar, note, query, chat)
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bot.loader import bot
from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Jerusalem")
_CONFIRM_TTL = 120  # seconds

_MODEL_DISPLAY = {
    "gemini-3-flash-preview": "Gemini 3 Flash",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    "moonshotai/kimi-k2-instruct-0905": "Kimi K2",
}


# ---------------------------------------------------------------------------
# Confirmation persistence (Supabase-backed, survives restarts)
# ---------------------------------------------------------------------------

def save_confirmation(user_id: int, action_name: str, action_data: dict) -> None:
    """Persist a pending confirmation so the user can reply asynchronously."""
    try:
        supabase.table("pending_confirmations").upsert({
            "user_id": user_id,
            "action_name": action_name,
            "action_data": action_data,
            "created_at": datetime.now(TZ).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        logger.warning(f"Failed to save confirmation to DB: {e}")


def get_confirmation(user_id: int) -> tuple[str, dict] | None:
    """Retrieve and consume a pending confirmation (returns None if expired)."""
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
        created = datetime.fromisoformat(row["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=TZ)
        age = (datetime.now(TZ) - created).total_seconds()
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
        if age > _CONFIRM_TTL:
            return None
        return (row["action_name"], row["action_data"])
    except Exception as e:
        logger.warning(f"Failed to get confirmation from DB: {e}")
        return None


def cancel_confirmation(user_id: int) -> None:
    """Cancel any pending confirmation for the user."""
    try:
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Voice transcription
# ---------------------------------------------------------------------------

async def transcribe_voice(file_id: str) -> str | None:
    """Download a Telegram voice message and transcribe it via Gemini."""
    try:
        import io

        from google import genai
        from google.genai import types as genai_types

        file_info = await bot.get_file(file_id)
        file_bytes = io.BytesIO()
        await bot.download_file(file_info.file_path, file_bytes)
        audio_data = file_bytes.getvalue()

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL_FALLBACK,
                contents=[
                    genai_types.Content(parts=[
                        genai_types.Part.from_bytes(data=audio_data, mime_type="audio/ogg"),
                        genai_types.Part.from_text(
                            "Transcribe this audio message to text. "
                            "Return ONLY the transcribed text, nothing else. "
                            "The language is likely Hebrew."
                        ),
                    ]),
                ],
            ),
            timeout=15,
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Status message helper
# ---------------------------------------------------------------------------

async def _edit_status(chat_id: int, message_id: int, text: str) -> None:
    """Edit the status message, appending which LLM model was used."""
    try:
        from app.core.llm import last_model_used
        model = last_model_used.get("")
        if model:
            short = _MODEL_DISPLAY.get(model, model)
            text += f"\n\n({short})"
    except Exception:
        pass
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")


# ---------------------------------------------------------------------------
# Core processing pipeline
# ---------------------------------------------------------------------------

async def _handle_confirmation(
    text: str, user_id: int, update_id: int | None,
    edit_status,
) -> bool:
    """Handle yes/no confirmation replies. Returns True if handled."""
    from app.services.google_svc import GoogleService
    from app.services.memory_service import log_interaction
    from app.services.task_service import (
        complete_all_tasks,
        create_task,
        delete_task,
    )

    text_lower = text.strip().lower()
    if text_lower not in ("כן", "yes", "confirm", "אישור"):
        return False

    pending = get_confirmation(user_id)
    if not pending:
        return False

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
                bot_response = (
                    f"נקבע: {action_data['task_title']}\n{slot['day']} {slot['time']}\n{link}"
                    if link else "לא הצלחתי ליצור אירוע."
                )
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
    return True


async def _handle_url(
    text: str, urls: list[str], user_id: int, update_id: int | None,
    edit_status,
) -> None:
    """Extract, fetch, summarize and save URL content."""
    from app.services.archive_service import save_url_knowledge
    from app.services.memory_service import log_interaction
    from app.services.url_service import fetch_url_content, summarize_and_tag

    url = urls[0]
    try:
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
            tags_str = " ".join(f"#{t}" for t in result["tags"]) if result["tags"] else ""
            kp_str = "\n" + "\n".join(f"- {kp}" for kp in result["key_points"]) if result["key_points"] else ""
            await edit_status(f"נשמר: {fetched['title']}\n\n{result['summary']}{kp_str}\n\n{tags_str}")

        await log_interaction(
            user_id=user_id, user_message=text, bot_response="URL saved",
            action_type="note", intent_summary="URL save",
            telegram_update_id=update_id,
        )
    except Exception as e:
        logger.error(f"URL processing error: {e}")
        await edit_status("שגיאה בעיבוד הלינק.")


async def _dispatch_intent(
    text: str, intent, memory_context: str,
    user_id: int, update_id: int | None,
    edit_status,
) -> None:
    """Route the classified intent to the appropriate service handler."""
    from app.services.archive_service import save_note
    from app.services.memory_service import log_interaction
    from app.services.query_service import QueryService

    action_type = intent.classification.action_type
    bot_response = None

    # --- Ambiguous input ---
    if intent.classification.confidence < 0.55 and action_type not in ("chat",):
        suggestions = []
        if intent.task:
            suggestions.append(f"1. משימה: \"{intent.task.title}\"")
        if intent.query:
            suggestions.append(f"{'2' if suggestions else '1'}. שאלה: \"{intent.query.query[:50]}\"")
        suggestions.append(f"{len(suggestions) + 1}. שיחה חופשית")
        bot_response = "לא בטוח מה התכוונת. אפשרויות:\n" + "\n".join(suggestions) + "\n\nשלח את המספר או נסח מחדש."
        save_confirmation(user_id, "disambiguate", {
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

    # --- Task actions ---
    if action_type == "task" and intent.task:
        bot_response = await _handle_task_action(text, intent, user_id, edit_status)

    # --- Calendar ---
    elif action_type == "calendar" and intent.calendar:
        bot_response = await _handle_calendar_action(intent, user_id)

    # --- Note ---
    elif action_type == "note" and intent.note:
        saved = await save_note(user_id, intent.note.content, intent.note.tags)
        if saved:
            tags_str = " ".join(f"#{t}" for t in intent.note.tags)
            bot_response = f"נשמר: {intent.note.content}\n{tags_str}"
        else:
            bot_response = "לא הצלחתי לשמור."

    # --- Chat ---
    elif action_type == "chat":
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

    # --- Query ---
    elif action_type == "query":
        qs = QueryService(user_id)
        query_text = intent.query.query if intent.query else text
        target_date = intent.query.target_date if intent.query else None
        context_needed = intent.query.context_needed if intent.query else []
        archive_since = getattr(intent.query, "archive_since", None) if intent.query else None
        bot_response = await qs.answer_query(
            query_text, context_needed, target_date, memory_context,
            archive_since=archive_since,
        )

    else:
        bot_response = "לא בטוח מה לעשות עם זה."

    if bot_response:
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type=action_type, intent_summary=intent.classification.summary,
            telegram_update_id=update_id,
        )


async def _handle_task_action(text: str, intent, user_id: int, edit_status) -> str | None:
    """Process task-related actions (create, complete, delete, edit, schedule)."""
    from app.services.google_svc import GoogleService
    from app.services.task_service import (
        _match_task,
        complete_task,
        create_task,
        edit_task,
        get_pending_tasks,
    )

    action = getattr(intent.task, "action", "create")

    if action == "complete":
        result = await complete_task(user_id, intent.task.title)
        if result:
            return f"בוצע: {result['title']} ✅"
        pending = await get_pending_tasks(user_id, limit=20)
        if pending:
            task_list = "\n".join(f"{i + 1}. {t['title']}" for i, t in enumerate(pending))
            return f"לא מצאתי \"{intent.task.title}\" במשימות הפתוחות.\n\nהמשימות שלך:\n{task_list}"
        return f"לא מצאתי \"{intent.task.title}\" — אין משימות פתוחות בכלל."

    if action == "complete_all":
        pending = await get_pending_tasks(user_id, limit=50)
        count = len(pending) if pending else 0
        if count == 0:
            return "אין משימות פתוחות."
        save_confirmation(user_id, "complete_all", {})
        task_list = "\n".join(f"  - {t['title']}" for t in pending[:10])
        extra = f"\n  ... ועוד {count - 10}" if count > 10 else ""
        return f"עומד לסמן {count} משימות כבוצעו:\n{task_list}{extra}\n\nשלח 'כן' לאישור."

    if action == "delete":
        save_confirmation(user_id, "delete", {"title": intent.task.title})
        return f"עומד למחוק: \"{intent.task.title}\"\nשלח 'כן' לאישור."

    if action == "schedule":
        existing = await get_pending_tasks(user_id, limit=50)
        match = _match_task(existing, intent.task.title) if existing else None
        if not match:
            return f"לא מצאתי משימה בשם \"{intent.task.title}\" לתזמון."
        google = GoogleService(user_id)
        if not await google.authenticate():
            login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
            return f"צריך לחבר Google קודם:\n{login_url}"
        effort_map = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}
        effort_str = match.get("effort", "1h")
        duration = effort_map.get(effort_str, 60)
        slots = await google.find_free_slots(duration_minutes=duration, days_ahead=3, max_slots=3)
        if slots:
            lines = [f"חלונות פנויים ל-\"{match['title']}\" ({effort_str}):"]
            for i, s in enumerate(slots, 1):
                lines.append(f"{i}. {s['day']} {s['time']}")
            lines.append("\nשלח את המספר לקביעה, או אמור 'לא' לביטול.")
            save_confirmation(user_id, "schedule_task", {
                "task_title": match["title"],
                "slots": slots,
            })
            return "\n".join(lines)
        return "לא מצאתי חלונות פנויים ב-3 הימים הקרובים."

    if action == "edit":
        updates: dict = {}
        if getattr(intent.task, "new_title", None):
            updates["title"] = intent.task.new_title
        if getattr(intent.task, "new_due_date", None):
            updates["due_date"] = intent.task.new_due_date
        if getattr(intent.task, "new_priority", None) is not None:
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
            return f"עודכן: {result['title']}\n" + ", ".join(changes)
        pending = await get_pending_tasks(user_id, limit=20)
        if pending:
            task_list = "\n".join(f"{i + 1}. {t['title']}" for i, t in enumerate(pending))
            return f"לא מצאתי \"{intent.task.title}\".\n\nהמשימות שלך:\n{task_list}"
        return f"לא מצאתי \"{intent.task.title}\" — אין משימות פתוחות."

    # Default: create task (with duplicate detection)
    task_data = intent.task.model_dump()
    existing = await get_pending_tasks(user_id, limit=50)
    if existing:
        dup = _match_task(existing, task_data.get("title", ""))
        if dup:
            save_confirmation(user_id, "create_task", task_data)
            return f"כבר יש משימה דומה: \"{dup['title']}\"\nרוצה שאוסיף בכל זאת?"

    task = await create_task(user_id, task_data)
    if task:
        due_str = ""
        if task.get("due_at"):
            try:
                dt = datetime.fromisoformat(task["due_at"])
                due_str = f"\n📅 {dt.strftime('%a %b %d, %H:%M')}"
            except (ValueError, TypeError):
                due_str = f"\n📅 {task['due_at']}"
        recurrence = task_data.get("recurrence")
        recur_str = f"\n🔄 חוזר {recurrence}" if recurrence else ""
        effort = task_data.get("effort")
        effort_str = f"\n⏱ {effort}" if effort else ""
        return f"נוסף: {task['title']}{due_str}{recur_str}{effort_str}"
    return "משהו השתבש בשמירת המשימה. נסה שוב?"


async def _handle_calendar_action(intent, user_id: int) -> str:
    """Create a Google Calendar event from the classified intent."""
    from app.services.google_svc import GoogleService

    google = GoogleService(user_id)
    if not await google.authenticate():
        login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
        return f"צריך לחבר Google קודם:\n{login_url}"

    event_data = intent.calendar
    try:
        start_dt = datetime.strptime(event_data.start_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            start_dt = datetime.fromisoformat(event_data.start_time)
        except Exception:
            start_dt = None

    end_dt = None
    if event_data.end_time:
        try:
            end_dt = datetime.strptime(event_data.end_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                end_dt = datetime.fromisoformat(event_data.end_time)
            except Exception:
                end_dt = None

    if not start_dt:
        return f"לא הצלחתי לפרסר את התאריך: {event_data.start_time}"

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
                remainder = f" {mins % 60}m" if mins % 60 else ""
                duration_str = f" ({mins // 60}h{remainder})"
            else:
                duration_str = f" ({mins}m)"
        loc_str = f"\n📍 {event_data.location}" if event_data.location else ""
        return f"נקבע: {event_data.summary}\n{start_dt.strftime('%d/%m %H:%M')}{duration_str}{loc_str}\n{link}"
    return "לא הצלחתי ליצור אירוע ביומן."


# ---------------------------------------------------------------------------
# Main entry point — called from FastAPI webhook
# ---------------------------------------------------------------------------

async def process_update(update_data: dict) -> None:
    """Process a single Telegram update (runs as a background task).

    Pipeline:
    1. Extract message text (or transcribe voice)
    2. Authenticate user via whitelist
    3. Send typing indicator + status placeholder
    4. Check for duplicate webhook delivery
    5. Handle confirmation replies / URL interception
    6. Classify intent via LLM router
    7. Dispatch to appropriate service
    """
    chat_id = None
    status_msg_id = None

    try:
        from aiogram import types as aio_types

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
            transcribed = await transcribe_voice(voice["file_id"])
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

        # Non-text updates → delegate to aiogram dispatcher
        if not chat_id or not text:
            from app.bot.loader import dp
            update = aio_types.Update(**update_data)
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

        # Closure for editing the status message
        async def edit_status(new_text: str) -> None:
            await _edit_status(chat_id, status_msg_id, new_text)

        # --- Core processing with 55s timeout ---
        async def _process_core() -> None:
            from app.services.memory_service import get_relevant_insights
            from app.services.router_service import route_intent
            from app.services.url_service import extract_urls

            await bot.send_chat_action(chat_id=chat_id, action="typing")

            # Confirmation check
            if await _handle_confirmation(text, user_id, update_id, edit_status):
                return

            cancel_confirmation(user_id)

            # URL interception
            urls = extract_urls(text)
            if urls:
                await _handle_url(text, urls, user_id, update_id, edit_status)
                return

            # Parallel intent classification + memory retrieval
            intent_result, memory_result = await asyncio.gather(
                route_intent(text, user_id=user_id),
                get_relevant_insights(user_id=user_id, action_type="query", query_text=text),
                return_exceptions=True,
            )

            if isinstance(intent_result, Exception):
                logger.error(f"Intent routing failed: {intent_result}")
                from app.models.router_models import ActionClassification, QueryPayload, RouterResponse
                intent_result = RouterResponse(
                    classification=ActionClassification(action_type="query", confidence=0.5, summary="Fallback"),
                    query=QueryPayload(query=text, context_needed=[]),
                )
            if isinstance(memory_result, Exception):
                logger.error(f"Memory fetch failed: {memory_result}")
                memory_result = ""

            await _dispatch_intent(
                text, intent_result, memory_result,
                user_id, update_id, edit_status,
            )

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
        if chat_id and status_msg_id:
            try:
                await bot.edit_message_text(
                    text="משהו השתבש. נסה שוב.",
                    chat_id=chat_id, message_id=status_msg_id,
                )
            except Exception:
                pass
