import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException

from app.bot.loader import bot
from app.core.config import settings
from app.services.memory_service import extract_follow_ups, get_pending_follow_ups, run_daily_reflection
from app.services.task_service import get_overdue_tasks

router = APIRouter(prefix="/api/cron", tags=["cron"])
logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Jerusalem")

async def verify_cron_secret(authorization: str = Header(None)):
    expected = f"Bearer {settings.M_WEBHOOK_SECRET}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid cron secret")

async def _check_task_reminders(user_id: int) -> int:
    """Send reminders for overdue tasks. Returns count sent."""
    tasks = await get_overdue_tasks(user_id)
    if not tasks:
        return 0

    for task in tasks:
        try:
            due_str = ""
            if task.get('due_at'):
                try:
                    dt = datetime.fromisoformat(task['due_at'])
                    due_str = f"\nהיה אמור להיות ב: {dt.strftime('%d/%m %H:%M')}"
                except (ValueError, TypeError):
                    due_str = f"\nהיה אמור להיות ב: {task['due_at']}"
            msg = (
                f"🚨 עדיין לא עשית את זה:\n"
                f"{task['title']}{due_str}\n\n"
                f"תטפל בזה או תגיד לי למחוק 💪"
            )
            await bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to send alert for task {task.get('id')}: {e}")

    return len(tasks)


async def _check_email_alerts(user_id: int) -> int:
    """Check for urgent unread emails. Always uses Gmail (free) to avoid iGPT token costs on cron."""
    try:
        return await _check_email_alerts_gmail(user_id)
    except Exception as e:
        logger.error(f"Email alert check failed: {e}")
        return 0


async def _check_email_alerts_igpt(user_id: int) -> int | None:
    """Use iGPT to detect urgent emails semantically.

    Returns int (alerts sent) on success, None if iGPT can't access emails
    (triggers Gmail fallback).
    """
    from app.services import igpt_service as igpt

    answer = await igpt.ask(
        "Are there any urgent or time-sensitive unread emails in the last 30 minutes "
        "that need immediate attention? List each with sender, subject, and why it's "
        "urgent. If nothing is urgent, say 'No urgent emails.'"
    )
    if not answer:
        return None  # iGPT failed — fall back to Gmail

    lower = answer.lower()

    # iGPT can't access emails (not indexed yet) — fall back to Gmail
    no_access_phrases = [
        "don't have access", "do not have access", "don't have access",
        "i can't access", "i cannot access", "not have access",
        "check your email client", "no access",
    ]
    if any(phrase in lower for phrase in no_access_phrases):
        logger.info("iGPT has no email access yet, falling back to Gmail")
        return None

    # Nothing urgent — no alert needed (but iGPT is working)
    no_urgent_phrases = [
        "no urgent", "no new", "nothing urgent", "no time-sensitive",
        "no emails", "no unread",
    ]
    if any(phrase in lower for phrase in no_urgent_phrases):
        return 0

    msg = f"📧 Email Alert (iGPT)\n\n{answer}"
    await bot.send_message(chat_id=user_id, text=msg)
    return 1


async def _check_email_alerts_gmail(user_id: int) -> int:
    """Keyword-based urgent email detection via Gmail API."""
    from app.services.google_svc import GoogleService

    key_contacts_str = getattr(settings, "ALERT_KEY_CONTACTS", "")
    urgent_keywords_str = getattr(settings, "ALERT_URGENT_KEYWORDS", "urgent,asap,emergency,critical,deadline,immediately")

    key_contacts = [c.strip().lower() for c in key_contacts_str.split(",") if c.strip()]
    urgent_keywords = [k.strip().lower() for k in urgent_keywords_str.split(",") if k.strip()]

    if not key_contacts and not urgent_keywords:
        return 0

    google = GoogleService(user_id)
    await google.authenticate()
    emails = await google.get_recent_unread_emails(max_results=10, minutes_back=35)

    if not emails:
        return 0

    count = 0
    for email in emails:
        sender = email.get("from", "").lower()
        subject = email.get("subject", "").lower()
        snippet = email.get("snippet", "").lower()

        reason = None

        # Check key contacts
        for contact in key_contacts:
            if contact in sender:
                reason = f"key contact ({contact})"
                break

        # Check urgent keywords
        if not reason:
            for keyword in urgent_keywords:
                if keyword in subject or keyword in snippet:
                    reason = f"urgent keyword: \"{keyword}\""
                    break

        if reason:
            msg = (
                f"📧 Email Alert ({reason})\n"
                f"From: {email.get('from', '?')}\n"
                f"Subject: {email.get('subject', '?')}\n"
                f"{email.get('snippet', '')[:150]}"
            )
            await bot.send_message(chat_id=user_id, text=msg)
            count += 1

    return count


async def _check_stock_alerts(user_id: int) -> int:
    """Check for significant stock moves. Tracks per-ticker alerts in DB to survive restarts."""
    try:
        from app.core.database import supabase
        from app.services.market_service import fetch_market_data

        today_str = datetime.now(TZ).strftime("%Y-%m-%d")

        # Fetch already-alerted tickers from DB (persists across restarts)
        try:
            resp = (
                supabase.table("interaction_log")
                .select("bot_response")
                .eq("user_id", user_id)
                .eq("action_type", "stock_alert")
                .gte("created_at", f"{today_str}T00:00:00")
                .limit(1)
                .execute()
            )
            if resp.data:
                # Already sent a stock alert today
                return 0
        except Exception as e:
            logger.warning(f"Stock alert dedup check failed: {e}")

        threshold = getattr(settings, "STOCK_ALERT_THRESHOLD", 3.0)
        market = await fetch_market_data()

        movers = []
        for item_list in [market.get("indices", []), market.get("tickers", [])]:
            for item in item_list:
                pct = item.get("change_pct", 0)
                if abs(pct) >= threshold:
                    arrow = "🟢📈" if pct >= 0 else "🔴📉"
                    movers.append(f"{arrow} {item['name']}: {item.get('price', 0):,.2f} ({pct:+.1f}%)")

        if not movers:
            return 0

        msg = "📊 התראת שוק — תזוזות גדולות היום:\n" + "\n".join(movers)
        await bot.send_message(chat_id=user_id, text=msg)

        # Persist alert in DB so it survives server restarts
        try:
            supabase.table("interaction_log").insert({
                "user_id": user_id,
                "user_message": "stock_alert_cron",
                "bot_response": msg[:500],
                "action_type": "stock_alert",
            }).execute()
        except Exception as e:
            logger.warning(f"Stock alert dedup write failed: {e}")

        return len(movers)

    except Exception as e:
        logger.error(f"Stock alert check failed: {e}")
        return 0


async def _check_weather_alert(user_id: int) -> int:
    """Check for rain forecast. Returns 1 if alert sent, 0 otherwise."""
    try:
        import httpx

        from app.core.cache import cache_get, cache_set

        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if cache_get(f"weather_alert:{today_str}"):
            return 0  # Already alerted today

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": 32.0853,
                    "longitude": 34.7818,
                    "hourly": "precipitation_probability",
                    "forecast_days": 1,
                    "timezone": "Asia/Jerusalem",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        hourly = data.get("hourly", {})
        probs = hourly.get("precipitation_probability", [])
        times = hourly.get("time", [])

        now = datetime.now(TZ)
        # Check next 6 hours
        max_prob = 0
        rain_hours = []
        for i, (t, p) in enumerate(zip(times, probs)):
            try:
                hour_dt = datetime.fromisoformat(t).replace(tzinfo=TZ)
                if now <= hour_dt <= now + timedelta(hours=6):
                    if p and p > max_prob:
                        max_prob = p
                    if p and p >= 60:
                        rain_hours.append(hour_dt.strftime("%H:%M"))
            except (ValueError, TypeError):
                continue

        if max_prob >= 60:
            msg = (
                f"🌧 התראת מזג אוויר — צפוי גשם!\n"
                f"סיכוי למשקעים: עד {max_prob}%\n"
                f"שעות צפויות: {', '.join(rain_hours[:4])}\n"
                f"קח מטריה ☂️"
            )
            await bot.send_message(chat_id=user_id, text=msg)
            cache_set(f"weather_alert:{today_str}", True, 86400)
            return 1

        return 0

    except Exception as e:
        logger.error(f"Weather alert check failed: {e}")
        return 0


async def _check_followup_reminders(user_id: int) -> int:
    """Send reminders for overdue follow-ups. Returns count sent."""
    from app.core.database import supabase

    follow_ups = await get_pending_follow_ups(user_id, limit=5)
    if not follow_ups:
        return 0

    now = datetime.now(TZ)
    count = 0
    for fu in follow_ups:
        if count >= 3:
            break
        # Only remind if overdue and not reminded too many times
        if fu.get("reminded_count", 0) >= 3:
            continue
        if fu.get("due_at"):
            try:
                due = datetime.fromisoformat(fu["due_at"])
                if due.tzinfo is None:
                    due = due.replace(tzinfo=TZ)
                if due > now:
                    continue  # Not overdue yet
            except (ValueError, TypeError):
                pass

        try:
            due_str = ""
            if fu.get("due_at"):
                due_str = f"\nהיה אמור להיות ב: {fu['due_at'][:10]}"
            msg = (
                f"🔄 תזכורת המשך:\n"
                f"{fu['commitment']}{due_str}\n\n"
                f"עדיין על הצלחת — תטפל או תגיד לי לוותר"
            )
            await bot.send_message(chat_id=user_id, text=msg)

            # Update reminded count
            supabase.table("follow_ups").update({
                "reminded_count": fu.get("reminded_count", 0) + 1,
                "last_reminded_at": now.isoformat(),
            }).eq("id", fu["id"]).execute()

            count += 1
        except Exception as e:
            logger.error(f"Failed to send follow-up reminder: {e}")

    return count


@router.get("/check-reminders", dependencies=[Depends(verify_cron_secret)])
async def check_reminders():
    user_id = settings.TELEGRAM_USER_ID

    # Run all checks in parallel
    task_count, followup_count, email_alerts, stock_alerts, weather_alert = await asyncio.gather(
        _check_task_reminders(user_id),
        _check_followup_reminders(user_id),
        _check_email_alerts(user_id),
        _check_stock_alerts(user_id),
        _check_weather_alert(user_id),
        return_exceptions=True,
    )

    # Handle exceptions gracefully
    results = {}
    for name, val in [
        ("task_reminders", task_count),
        ("followup_reminders", followup_count),
        ("email_alerts", email_alerts),
        ("stock_alerts", stock_alerts),
        ("weather_alerts", weather_alert),
    ]:
        if isinstance(val, Exception):
            logger.error(f"{name} failed: {val}")
            results[name] = 0
        else:
            results[name] = val

    total = sum(results.values())
    if total == 0:
        return {"status": "ok", "message": "No reminders or alerts"}

    return {"status": "ok", **results}

@router.get("/meeting-prep", dependencies=[Depends(verify_cron_secret)])
async def meeting_prep():
    """Check for upcoming meetings and send prep briefs."""
    user_id = settings.TELEGRAM_USER_ID

    try:
        from app.services.briefing_service import generate_meeting_prep
        messages = await generate_meeting_prep(user_id)

        for msg in messages:
            try:
                await bot.send_message(chat_id=user_id, text=msg)
            except Exception as e:
                logger.error(f"Failed to send meeting prep: {e}")

        return {"status": "ok", "preps_sent": len(messages)}

    except Exception as e:
        logger.error(f"Meeting prep error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/daily-brief", dependencies=[Depends(verify_cron_secret)])
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
            f"בריפינג בוקר\n\n"
            f"📅 יומן:\n{calendar_str}\n\n"
            f"✅ משימות:\n{task_str}"
        )
        await bot.send_message(chat_id=user_id, text=msg)
        return {"status": "ok", "message": "Basic briefing sent (fallback)"}


@router.get("/heartbeat", dependencies=[Depends(verify_cron_secret)])
async def heartbeat():
    """Proactive check-in — mid-week nudge or evening wrap-up depending on time."""
    user_id = settings.TELEGRAM_USER_ID

    try:
        from app.services.heartbeat_service import generate_evening_wrapup, generate_goal_checkin

        now = datetime.now(TZ)
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


@router.get("/weekly-review", dependencies=[Depends(verify_cron_secret)])
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


@router.get("/daily-reflection", dependencies=[Depends(verify_cron_secret)])
async def daily_reflection():
    user_id = settings.TELEGRAM_USER_ID

    # Run reflection + follow-up extraction in parallel
    result, followup_count = await asyncio.gather(
        run_daily_reflection(user_id),
        extract_follow_ups(user_id),
        return_exceptions=True,
    )

    if isinstance(result, Exception):
        logger.error(f"Reflection failed: {result}")
        result = {"interactions_analyzed": 0, "new_insights": 0, "reinforced_insights": 0}
    if isinstance(followup_count, Exception):
        logger.error(f"Follow-up extraction failed: {followup_count}")
        followup_count = 0

    # Send Telegram summary if new insights or follow-ups were found
    if result["new_insights"] > 0 or result["reinforced_insights"] > 0 or followup_count > 0:
        try:
            msg = (
                f"🧠 סיכום רפלקציה יומית\n"
                f"אינטראקציות שנותחו: {result['interactions_analyzed']}\n"
                f"תובנות חדשות: {result['new_insights']}\n"
                f"תובנות שהתחזקו: {result['reinforced_insights']}\n"
                f"פעולות המשך: {followup_count}"
            )
            await bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.error(f"Failed to send reflection summary: {e}")

    return {"status": "ok", **result, "follow_ups_extracted": followup_count}
