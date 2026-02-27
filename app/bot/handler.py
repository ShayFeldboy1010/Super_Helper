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
import html as _html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bot.loader import bot
from app.core.config import settings
from app.core.database import supabase

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Jerusalem")
_CONFIRM_TTL = 600  # seconds (10 minutes)

_MODEL_DISPLAY = {
    "gemini-3-flash-preview": "Gemini 3 Flash",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    "moonshotai/kimi-k2-instruct-0905": "Kimi K2",
}


# ---------------------------------------------------------------------------
# Greeting fast-path — skip LLM router for obvious greetings
# ---------------------------------------------------------------------------

_GREETINGS = {
    "היי", "הי", "שלום", "בוקר טוב", "ערב טוב", "לילה טוב", "מה נשמע",
    "מה קורה", "אהלן", "יו", "תודה", "תודה רבה", "מה העניינים",
    "hi", "hey", "hello", "yo", "thanks", "good morning", "sup",
}


def _is_greeting(text: str) -> bool:
    """Return True if text is an obvious greeting/casual message."""
    return text.strip().lower().rstrip("!?.,") in _GREETINGS


# ---------------------------------------------------------------------------
# Typing keep-alive — sends "typing" action every 4s until stopped
# ---------------------------------------------------------------------------

async def _typing_keepalive(chat_id: int, stop_event: asyncio.Event) -> None:
    """Send typing indicator every 4s until stop_event is set."""
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4)
                break  # event was set
            except asyncio.TimeoutError:
                pass  # loop and send again
    except Exception:
        pass  # never crash — typing is cosmetic


# ---------------------------------------------------------------------------
# Confirmation persistence (Supabase-backed, survives restarts)
# ---------------------------------------------------------------------------

def save_confirmation(user_id: int, action_name: str, action_data: dict) -> None:
    """Persist a pending confirmation so the user can reply asynchronously."""
    try:
        action_data = {**action_data, "_ts": time.time()}
        row = {
            "user_id": user_id,
            "action_name": action_name,
            "action_data": action_data,
            "created_at": datetime.now(TZ).isoformat(),
        }
        # Delete-then-insert is more reliable than upsert (avoids silent failure
        # if user_id lacks a UNIQUE constraint for on_conflict)
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
        resp = supabase.table("pending_confirmations").insert(row).execute()
        logger.info(f"Confirmation saved: action={action_name}, user={user_id}, rows={len(resp.data or [])}")
    except Exception as e:
        logger.error(f"Failed to save confirmation to DB: {e}", exc_info=True)


def get_confirmation(user_id: int) -> tuple[str, dict] | None:
    """Retrieve and consume a pending confirmation (returns None if expired)."""
    import json

    try:
        resp = (
            supabase.table("pending_confirmations")
            .select("action_name, action_data, created_at")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            logger.info(f"No pending confirmation for user={user_id}")
            return None
        row = resp.data[0]
        action_data = row["action_data"]

        # Defensive: Supabase may return JSONB as a string
        if isinstance(action_data, str):
            try:
                action_data = json.loads(action_data)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"action_data is unparseable string: {action_data[:100]}")
                action_data = {}

        if not isinstance(action_data, dict):
            logger.warning(f"action_data unexpected type: {type(action_data)}")
            action_data = {}

        # Prefer embedded Unix timestamp (timezone-safe) over created_at
        ts = action_data.get("_ts")
        if ts:
            age = time.time() - ts
        else:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=ZoneInfo("UTC"))
            age = (datetime.now(ZoneInfo("UTC")) - created).total_seconds()

        logger.info(f"Confirmation found: action={row['action_name']}, age={age:.0f}s, user={user_id}")

        if age > _CONFIRM_TTL:
            supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
            logger.info(f"Confirmation expired (age={age:.0f}s > TTL={_CONFIRM_TTL}s)")
            return None
        supabase.table("pending_confirmations").delete().eq("user_id", user_id).execute()
        return (row["action_name"], action_data)
    except Exception as e:
        logger.error(f"Failed to get confirmation from DB: {e}", exc_info=True)
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
    """Edit the status message with HTML formatting, appending which LLM model was used."""
    try:
        from app.core.llm import last_model_used
        model = last_model_used.get("")
        if model:
            short = _MODEL_DISPLAY.get(model, model)
            text += f"\n\n<i>({short})</i>"
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id,
            parse_mode="HTML",
        )
    except Exception as e:
        # Fallback: try without parse_mode in case HTML is malformed
        try:
            import re
            plain = re.sub(r'<[^>]+>', '', text)
            await bot.edit_message_text(text=plain, chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.error(f"Failed to edit message: {e}")


# ---------------------------------------------------------------------------
# Stock alert preference handling
# ---------------------------------------------------------------------------

_ALERT_DISABLE_KEYWORDS = {"תפסיק התראות", "בלי מניות", "stop alerts", "disable stock", "תפסיק מניות", "בטל התראות מניות"}
_ALERT_ENABLE_KEYWORDS = {"תחזיר התראות", "enable alerts", "חדש מניות", "הפעל התראות מניות", "תפעיל מניות"}


async def _handle_alert_preference(
    text: str, user_id: int, update_id: int | None, edit_status,
) -> bool:
    """Detect stock alert preference changes. Returns True if handled."""
    from app.services.memory_service import log_interaction

    text_lower = text.strip().lower()

    disable = any(kw in text_lower for kw in _ALERT_DISABLE_KEYWORDS)
    enable = any(kw in text_lower for kw in _ALERT_ENABLE_KEYWORDS)

    if not disable and not enable:
        return False

    try:
        if disable:
            supabase.table("permanent_insights").upsert({
                "user_id": user_id,
                "category": "preference",
                "insight": "stock_alerts_disabled",
                "source_summary": "User requested to stop stock alerts",
            }, on_conflict="user_id,insight").execute()
            bot_response = "התראות מניות כבויות. שלח 'תחזיר התראות' להפעלה מחדש."
        else:
            supabase.table("permanent_insights").delete().eq(
                "user_id", user_id,
            ).eq("insight", "stock_alerts_disabled").execute()
            bot_response = "התראות מניות הופעלו מחדש."
    except Exception as e:
        logger.error(f"Alert preference update failed: {e}")
        bot_response = "שגיאה בעדכון העדפות. נסה שוב."

    await edit_status(bot_response)
    await log_interaction(
        user_id=user_id, user_message=text, bot_response=bot_response,
        action_type="system", intent_summary="Alert preference change",
        telegram_update_id=update_id,
    )
    return True


# ---------------------------------------------------------------------------
# Core processing pipeline
# ---------------------------------------------------------------------------

async def _handle_confirmation(
    text: str, user_id: int, update_id: int | None,
    edit_status,
) -> bool:
    """Handle confirmation replies (yes/no/slot number). Returns True if handled."""
    from app.services.google_svc import GoogleService
    from app.services.memory_service import log_interaction

    text_stripped = text.strip()
    text_lower = text_stripped.lower()
    # Strip punctuation for flexible yes/no matching (handles "כן!", "yes.", etc.)
    _PUNCT = str.maketrans("", "", "!?.,;:\"'(){}[]")
    text_norm = text_lower.translate(_PUNCT).strip()
    first_word = text_norm.split()[0] if text_norm else ""

    _YES = {"כן", "yes", "confirm", "אישור", "ok", "אוקי", "בטח", "sure", "yep", "כ"}
    _NO = {"לא", "no", "cancel", "ביטול", "לבטל", "nope"}

    # Check for pending confirmation FIRST
    pending = get_confirmation(user_id)
    if not pending:
        return False

    action_name, action_data = pending
    logger.info(f"Confirmation found: action={action_name}, text='{text_stripped}', norm='{text_norm}'")

    # --- Cancel / No ---
    if text_norm in _NO or first_word in _NO:
        cancel_confirmation(user_id)  # already consumed, but ensure cleanup
        bot_response = "בוטל."
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type="system", intent_summary=f"Cancelled {action_name}",
            telegram_update_id=update_id,
        )
        return True

    # Disambiguate: user picks option 1/2/3
    if action_name == "disambiguate":
        chosen = None
        for ch in text_stripped:
            if ch in ("1", "2", "3"):
                chosen = ch
                break
        if chosen and chosen in action_data.get("options", {}):
            # Re-route — return False so the original text goes through normal pipeline
            # but force the chosen action by updating the text hint
            original = action_data.get("original_text", text)
            chosen_type = action_data["options"][chosen]
            bot_response = f"הבנתי, מעבד כ-{chosen_type}..."
            await edit_status(bot_response)
            await log_interaction(
                user_id=user_id, user_message=text, bot_response=bot_response,
                action_type="system", intent_summary=f"Disambiguate → {chosen_type}",
                telegram_update_id=update_id,
            )
            # Now re-process the original text with a forced action type
            from app.services.memory_service import get_relevant_insights
            from app.services.router_service import route_intent
            intent = await route_intent(original, user_id=user_id)
            memory_ctx = await get_relevant_insights(
                user_id=user_id, action_type=chosen_type, query_text=original,
            )
            # Override the action type to what the user chose
            intent.classification.action_type = chosen_type
            intent.classification.confidence = 0.95
            await _dispatch_intent(original, intent, memory_ctx, user_id, update_id, edit_status)
            return True
        # Not a valid choice — re-save and let it pass through
        save_confirmation(user_id, action_name, action_data)
        return False

    # task_needs_time: user provides the time for a pending reminder
    if action_name == "task_needs_time":
        title = action_data.get("title", "")
        # Re-route through the router to parse the combined message
        from app.services.router_service import route_intent
        combined = f"תזכיר לי {title} {text_stripped}"
        intent = await route_intent(combined, user_id=user_id)

        safe_title = _html.escape(title)
        if intent.classification.action_type == "task" and intent.task:
            start_dt = _parse_task_datetime(intent.task.due_date, intent.task.time)
            if start_dt:
                from datetime import timedelta
                google_svc = GoogleService(user_id)
                if await google_svc.authenticate():
                    end_dt = start_dt + timedelta(minutes=30)
                    link = await google_svc.create_calendar_event(title, start_dt, end_dt=end_dt)
                    bot_response = (
                        f"<b>נקבע: {safe_title}</b>\n📅 {start_dt.strftime('%d/%m %H:%M')}\n<a href=\"{link}\">📅 לפתוח ביומן</a>"
                        if link else "לא הצלחתי ליצור אירוע ביומן."
                    )
                else:
                    bot_response = "שגיאה בחיבור ל-Google."
            else:
                # Still no time — ask again
                save_confirmation(user_id, "task_needs_time", {"title": title})
                bot_response = f"לא הבנתי את הזמן. מתי לקבוע את <b>{safe_title}</b>? (למשל: מחר ב-10, היום ב-14:00)"
        else:
            # Router didn't parse as task — try raw datetime parse
            start_dt = _parse_task_datetime(text_stripped, None)
            if start_dt:
                from datetime import timedelta
                google_svc = GoogleService(user_id)
                if await google_svc.authenticate():
                    end_dt = start_dt + timedelta(minutes=30)
                    link = await google_svc.create_calendar_event(title, start_dt, end_dt=end_dt)
                    bot_response = (
                        f"<b>נקבע: {safe_title}</b>\n📅 {start_dt.strftime('%d/%m %H:%M')}\n<a href=\"{link}\">📅 לפתוח ביומן</a>"
                        if link else "לא הצלחתי ליצור אירוע ביומן."
                    )
                else:
                    bot_response = "שגיאה בחיבור ל-Google."
            else:
                save_confirmation(user_id, "task_needs_time", {"title": title})
                bot_response = f"לא הבנתי את הזמן. מתי לקבוע את <b>{safe_title}</b>? (למשל: מחר ב-10, היום ב-14:00)"
    elif text_norm in _YES or first_word in _YES:
        bot_response = "בוצע."
    else:
        # Not a recognized confirmation input — re-save so it's not consumed
        logger.info(f"Confirmation not matched: action={action_name}, text_norm='{text_norm}', re-saving")
        save_confirmation(user_id, action_name, action_data)
        return False

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
        from app.services.query_service import QueryService

        qs = QueryService(user_id)
        recent_convo = await qs._get_recent_conversation(limit=5)

        system_parts = [CHIEF_OF_STAFF_IDENTITY]
        if recent_convo:
            system_parts.append(f"\n=== Recent conversation ===\n{recent_convo}")
        if memory_context:
            system_parts.append(f"\n=== Memory ===\n{memory_context}")

        chat_resp = await _llm(
            messages=[
                {"role": "system", "content": "\n".join(system_parts)},
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


def _parse_task_datetime(due_date_str: str | None, time_str: str | None) -> datetime | None:
    """Parse LLM date/time output into a timezone-aware datetime.

    Returns None if no date is provided or if date-only without time
    (triggers the 'מתי?' flow).
    """
    if not due_date_str:
        return None

    d = due_date_str.strip().lower()
    now = datetime.now(TZ)
    today = now.date()

    from datetime import timedelta

    target_date = None
    parsed_time = None

    if d == "today":
        target_date = today
    elif d == "tomorrow":
        target_date = today + timedelta(days=1)
    else:
        # Try "YYYY-MM-DD HH:MM:SS" (full datetime from LLM)
        try:
            parsed_dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
            target_date = parsed_dt.date()
            # Only count as having time if not midnight/9am default
            if parsed_dt.hour != 0 or parsed_dt.minute != 0:
                parsed_time = parsed_dt.time()
        except ValueError:
            # Try plain "YYYY-MM-DD"
            try:
                target_date = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                return None

    if not target_date:
        return None

    # Resolve time: explicit parsed_time > time_str > None (ask user)
    if parsed_time:
        return datetime.combine(target_date, parsed_time).replace(tzinfo=TZ)
    if time_str:
        try:
            t = datetime.strptime(time_str.strip(), "%H:%M").time()
            return datetime.combine(target_date, t).replace(tzinfo=TZ)
        except ValueError:
            pass
    # No time at all — return None to trigger "מתי?" flow
    return None


async def _handle_task_action(text: str, intent, user_id: int, edit_status) -> str | None:
    """Create a Google Calendar event from a task/reminder intent."""
    from app.services.google_svc import GoogleService

    title = intent.task.title or text
    due_date_str = intent.task.due_date
    time_str = intent.task.time

    # Check Google auth first
    google = GoogleService(user_id)
    if not await google.authenticate():
        login_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "/auth/login")
        return f"צריך לחבר Google קודם:\n{login_url}"

    # Parse datetime
    start_dt = _parse_task_datetime(due_date_str, time_str)

    if not start_dt:
        # No time specified — ask the user
        save_confirmation(user_id, "task_needs_time", {"title": title})
        return f"מתי לקבוע את <b>{_html.escape(title)}</b>?"

    # Create 30-min calendar event
    from datetime import timedelta
    end_dt = start_dt + timedelta(minutes=30)
    link = await google.create_calendar_event(title, start_dt, end_dt=end_dt)

    if link:
        safe_title = _html.escape(title)
        return f"<b>נקבע: {safe_title}</b>\n📅 {start_dt.strftime('%d/%m %H:%M')}\n<a href=\"{link}\">📅 לפתוח ביומן</a>"
    return "לא הצלחתי ליצור אירוע ביומן."


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
        safe_summary = _html.escape(event_data.summary)
        loc_str = f"\n📍 {_html.escape(event_data.location)}" if event_data.location else ""
        return (
            f"<b>נקבע: {safe_summary}</b>\n"
            f"📅 {start_dt.strftime('%d/%m %H:%M')}{duration_str}{loc_str}\n"
            f"<a href=\"{link}\">📅 לפתוח ביומן</a>"
        )
    return "לא הצלחתי ליצור אירוע ביומן."


# ---------------------------------------------------------------------------
# Code command interception (approve/reject/code)
# ---------------------------------------------------------------------------

async def _handle_code_commands(
    text: str, user_id: int, update_id: int | None,
    edit_status,
) -> bool:
    """Handle approve N, reject N, code status, code <instruction>. Returns True if handled."""
    import re

    from app.services.memory_service import log_interaction

    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # --- approve N ---
    m = re.match(r'^approve\s+(\d+)$', text_lower)
    if m:
        from app.services.code_task_service import approve_proposal
        idx = int(m.group(1))
        task = await approve_proposal(user_id, idx)
        if task:
            bot_response = f"אושר! נשלח ל-Claude Code.\nTask ID: {task['id'][:8]}..."
        else:
            bot_response = f"לא מצאתי הצעה #{idx} ממתינה היום."
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type="system", intent_summary=f"Approve proposal {idx}",
            telegram_update_id=update_id,
        )
        return True

    # --- reject N ---
    m = re.match(r'^reject\s+(\d+)$', text_lower)
    if m:
        from app.services.code_task_service import reject_proposal
        idx = int(m.group(1))
        ok = await reject_proposal(user_id, idx)
        bot_response = f"נדחה הצעה #{idx}." if ok else f"לא מצאתי הצעה #{idx} ממתינה היום."
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type="system", intent_summary=f"Reject proposal {idx}",
            telegram_update_id=update_id,
        )
        return True

    # --- code status ---
    if text_lower in ("code status", "code_status"):
        from app.services.code_task_service import format_recent_tasks_message, get_recent_tasks
        tasks = await get_recent_tasks(user_id, limit=5)
        bot_response = format_recent_tasks_message(tasks)
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type="system", intent_summary="Code task status",
            telegram_update_id=update_id,
        )
        return True

    # --- code <instruction> ---
    if text_lower.startswith("code "):
        from app.services.code_task_service import create_code_task, get_last_task_context
        instruction = text_stripped[5:].strip()
        if not instruction:
            return False
        # Include previous task context so Claude Code has conversation memory
        context = await get_last_task_context(user_id)
        if context:
            instruction = f"{context}New instruction from user: {instruction}"
        task = await create_code_task(user_id, instruction, source="manual")
        if task:
            bot_response = f"נשלח ל-Claude Code!\nTask ID: {task['id'][:8]}...\nאעדכן כשיסתיים."
        else:
            bot_response = "שגיאה ביצירת משימת קוד."
        await edit_status(bot_response)
        await log_interaction(
            user_id=user_id, user_message=text, bot_response=bot_response,
            action_type="system", intent_summary="Direct code task",
            telegram_update_id=update_id,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Main entry point — called from FastAPI webhook
# ---------------------------------------------------------------------------

async def process_update(update_data: dict) -> None:
    """Process a single Telegram update (runs as a background task).

    Pipeline:
    1. Extract message metadata
    2. Webhook deduplication (before any expensive work)
    3. Voice transcription (if audio)
    4. Authenticate user via whitelist
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

        # --- Webhook deduplication (before any expensive work) ---
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

        status = await bot.send_message(chat_id=chat_id, text="\u23f3")
        status_msg_id = status.message_id

        # Closure for editing the status message
        async def edit_status(new_text: str) -> None:
            await _edit_status(chat_id, status_msg_id, new_text)

        # --- Core processing with 55s timeout ---
        async def _process_core() -> None:
            from app.services.memory_service import get_relevant_insights
            from app.services.router_service import route_intent
            from app.services.url_service import extract_urls

            # Confirmation check (pending confirmations expire via TTL)
            if await _handle_confirmation(text, user_id, update_id, edit_status):
                return

            # --- Alert preference interception ---
            if await _handle_alert_preference(text, user_id, update_id, edit_status):
                return

            # --- Code command interception ---
            if await _handle_code_commands(text, user_id, update_id, edit_status):
                return

            # URL interception
            urls = extract_urls(text)
            if urls:
                await _handle_url(text, urls, user_id, update_id, edit_status)
                return

            # Greeting fast-path — skip LLM router for obvious greetings
            if _is_greeting(text):
                from app.models.router_models import ActionClassification, RouterResponse
                intent_result = RouterResponse(
                    classification=ActionClassification(
                        action_type="chat", confidence=0.99, summary="Greeting",
                    ),
                )
                memory_result = await get_relevant_insights(
                    user_id=user_id, action_type="chat", query_text=text,
                )
                if isinstance(memory_result, Exception):
                    memory_result = ""
                await _dispatch_intent(
                    text, intent_result, memory_result,
                    user_id, update_id, edit_status,
                )
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

            # Progressive status: show what we're doing
            action_type = intent_result.classification.action_type
            if action_type == "query":
                try:
                    await bot.edit_message_text(
                        text="📡 אוסף מידע...",
                        chat_id=chat_id, message_id=status_msg_id,
                    )
                except Exception:
                    pass
            elif action_type in ("task", "calendar"):
                try:
                    await bot.edit_message_text(
                        text="🔍 מנתח...",
                        chat_id=chat_id, message_id=status_msg_id,
                    )
                except Exception:
                    pass

            await _dispatch_intent(
                text, intent_result, memory_result,
                user_id, update_id, edit_status,
            )

        # Start typing keep-alive
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(_typing_keepalive(chat_id, stop_typing))

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
        finally:
            stop_typing.set()
            typing_task.cancel()

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
