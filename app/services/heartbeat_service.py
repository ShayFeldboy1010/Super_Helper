"""Proactive heartbeat messages — the bot reaches out, not just responds."""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from groq import AsyncGroq

from app.core.config import settings
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.core.database import supabase
from app.services.task_service import get_pending_tasks, get_overdue_tasks
from app.services.google_svc import GoogleService
from app.services.memory_service import get_relevant_insights

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)
TZ = ZoneInfo("Asia/Jerusalem")


async def generate_weekly_review(user_id: int) -> str | None:
    """Sunday evening — summarize the week, suggest next week priorities."""
    try:
        # Fetch this week's interactions
        resp = (
            supabase.table("interaction_log")
            .select("user_message, action_type, intent_summary, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        interactions = resp.data or []

        # Fetch completed + pending tasks
        pending = await get_pending_tasks(user_id, limit=10)
        overdue = await get_overdue_tasks(user_id)

        # Fetch insights
        insights = await get_relevant_insights(user_id, "query")

        # Build context
        interaction_summary = "\n".join(
            [f"- [{ix['action_type']}] {ix['intent_summary'] or ix['user_message'][:50]}"
             for ix in interactions[:20]]
        )
        pending_str = "\n".join([f"- {t['title']}" for t in pending]) if pending else "אין"
        overdue_str = "\n".join([f"- {t['title']}" for t in overdue]) if overdue else "אין"

        prompt = (
            f"הנה סיכום השבוע של שי:\n\n"
            f"אינטראקציות השבוע ({len(interactions)}):\n{interaction_summary}\n\n"
            f"משימות פתוחות:\n{pending_str}\n\n"
            f"משימות שעבר זמנן:\n{overdue_str}\n\n"
            f"תובנות קיימות:\n{insights or 'אין עדיין'}\n\n"
            f"כתוב סיכום שבועי קצר לשי. כולל:\n"
            f"1. מה בלט השבוע (מתוך האינטראקציות)\n"
            f"2. מה נשאר פתוח וצריך תשומת לב\n"
            f"3. 2-3 המלצות לשבוע הקרוב\n"
            f"תהיה ישיר, אישי, כמו חבר טוב שמכיר אותו."
        )

        chat = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            temperature=0.7,
        )
        return chat.choices[0].message.content

    except Exception as e:
        logger.error(f"Weekly review error: {e}")
        return None


async def generate_goal_checkin(user_id: int) -> str | None:
    """Mid-week check-in — are high-priority tasks on track?"""
    try:
        pending = await get_pending_tasks(user_id, limit=10)
        overdue = await get_overdue_tasks(user_id)

        if not pending and not overdue:
            return None  # Nothing to nudge about

        # Get calendar for today
        google = GoogleService(user_id)
        if await google.authenticate():
            events = await google.get_todays_events()
        else:
            events = []

        insights = await get_relevant_insights(user_id, "task")

        pending_str = "\n".join(
            [f"- {t['title']} (עדיפות: {t.get('priority', 0)}, עד: {t.get('due_at', 'ללא')})"
             for t in pending]
        )
        overdue_str = "\n".join([f"- ⚠️ {t['title']} (היה עד: {t.get('due_at')})" for t in overdue]) if overdue else "אין — כל הכבוד"
        events_str = "\n".join(events) if events else "יומן פנוי"

        prompt = (
            f"זה צ'ק-אין אמצע שבוע לשי.\n\n"
            f"משימות פתוחות:\n{pending_str}\n\n"
            f"משימות שעבר זמנן:\n{overdue_str}\n\n"
            f"יומן היום:\n{events_str}\n\n"
            f"תובנות:\n{insights or 'אין'}\n\n"
            f"כתוב הודעה קצרה וישירה — נאדג' ידידותי.\n"
            f"אם יש משימות שעבר זמנן — תזכיר בעדינות אבל בבירור.\n"
            f"אם הכל בסדר — מילה טובה קצרה.\n"
            f"תהיה כמו חבר שאכפת לו, לא כמו אפליקציה."
        )

        chat = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            temperature=0.7,
        )
        return chat.choices[0].message.content

    except Exception as e:
        logger.error(f"Goal check-in error: {e}")
        return None


async def generate_evening_wrapup(user_id: int) -> str | None:
    """Evening wrap-up — what happened today, what's tomorrow."""
    try:
        now = datetime.now(TZ)

        # Today's interactions
        today_str = now.strftime("%Y-%m-%d")
        resp = (
            supabase.table("interaction_log")
            .select("user_message, action_type, intent_summary")
            .eq("user_id", user_id)
            .gte("created_at", f"{today_str}T00:00:00")
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )
        todays = resp.data or []

        # Tomorrow's calendar
        google = GoogleService(user_id)
        tomorrow_events = []
        if await google.authenticate():
            from datetime import timedelta
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow_events = await google.get_events_for_date(tomorrow)

        # Overdue tasks
        overdue = await get_overdue_tasks(user_id)

        if not todays and not overdue and len(tomorrow_events) <= 1:
            return None  # Quiet day, don't bother

        today_summary = "\n".join(
            [f"- {ix.get('intent_summary') or ix['user_message'][:40]}" for ix in todays]
        ) if todays else "יום שקט"
        tomorrow_str = "\n".join(tomorrow_events) if tomorrow_events else "יומן פנוי"
        overdue_str = "\n".join([f"- {t['title']}" for t in overdue]) if overdue else "אין"

        prompt = (
            f"סיכום ערב לשי (עכשיו {now.strftime('%H:%M')}):\n\n"
            f"מה היה היום ({len(todays)} אינטראקציות):\n{today_summary}\n\n"
            f"מחר ביומן:\n{tomorrow_str}\n\n"
            f"דברים שנשארו פתוחים:\n{overdue_str}\n\n"
            f"כתוב הודעת ערב קצרה — סיכום + הצצה למחר.\n"
            f"אם יש משהו קריטי למחר, הדגש אותו.\n"
            f"טון: רגוע, ישיר, כמו חבר שעושה סינכרון בסוף היום."
        )

        chat = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            temperature=0.7,
        )
        return chat.choices[0].message.content

    except Exception as e:
        logger.error(f"Evening wrap-up error: {e}")
        return None
