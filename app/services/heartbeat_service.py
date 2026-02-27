"""Proactive heartbeat messages — the bot reaches out, not just responds."""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.database import supabase
from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.services.google_svc import GoogleService
from app.services.market_service import fetch_market_data
from app.services.memory_service import get_pending_follow_ups, get_relevant_insights

logger = logging.getLogger(__name__)
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

        # Fetch insights
        insights = await get_relevant_insights(user_id, "query")

        # Build context
        interaction_summary = "\n".join(
            [f"- [{ix['action_type']}] {ix['intent_summary'] or ix['user_message'][:50]}"
             for ix in interactions[:20]]
        )

        prompt = (
            f"Here's Shay's weekly summary:\n\n"
            f"This week's interactions ({len(interactions)}):\n{interaction_summary}\n\n"
            f"Existing insights:\n{insights or 'None yet'}\n\n"
            f"Write a short weekly review for Shay. Include:\n"
            f"1. What stood out this week (from the interactions)\n"
            f"2. What's still open and needs attention\n"
            f"3. 2-3 recommendations for the coming week\n"
            f"Be direct, personal, like a good friend who knows him."
        )

        chat = await llm_call(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            timeout=15,

        )
        return chat.choices[0].message.content if chat else None

    except Exception as e:
        logger.error(f"Weekly review error: {e}")
        return None


async def generate_goal_checkin(user_id: int) -> str | None:
    """Mid-week check-in — how's the day looking?"""
    try:
        # Get calendar for today
        google = GoogleService(user_id)
        if await google.authenticate():
            events = await google.get_todays_events()
        else:
            events = []

        if not events:
            return None  # Nothing to nudge about

        insights = await get_relevant_insights(user_id, "query")

        events_str = "\n".join(events) if events else "Calendar is clear"

        prompt = (
            f"Mid-week check-in for Shay.\n\n"
            f"Today's calendar:\n{events_str}\n\n"
            f"Insights:\n{insights or 'None'}\n\n"
            f"Write a short, direct message — a friendly nudge.\n"
            f"If the day looks busy — note what's important.\n"
            f"If everything's light — a quick good word.\n"
            f"Be like a friend who cares, not like an app."
        )

        chat = await llm_call(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            timeout=15,

        )
        return chat.choices[0].message.content if chat else None

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
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow_events = await google.get_events_for_date(tomorrow)

        # Parallel fetch: follow-ups, market data
        follow_ups, market = await asyncio.gather(
            get_pending_follow_ups(user_id, limit=5),
            fetch_market_data(),
            return_exceptions=True,
        )
        if isinstance(follow_ups, Exception):
            follow_ups = []
        if isinstance(market, Exception):
            market = {"indices": [], "tickers": []}

        if not todays and len(tomorrow_events) <= 1:
            return None  # Quiet day, don't bother

        today_summary = "\n".join(
            [f"- {ix.get('intent_summary') or ix['user_message'][:40]}" for ix in todays]
        ) if todays else "Quiet day"
        tomorrow_str = "\n".join(tomorrow_events) if tomorrow_events else "Calendar is clear"

        # Format follow-ups
        followup_str = "None"
        if follow_ups:
            followup_str = "\n".join(
                f"- {fu['commitment']}" + (f" (due: {fu['due_at'][:10]})" if fu.get("due_at") else "")
                for fu in follow_ups
            )

        # Notable market movers (>= 2%)
        movers = []
        for item_list in [market.get("indices", []), market.get("tickers", [])]:
            for item in item_list:
                if abs(item.get("change_pct", 0)) >= 2:
                    arrow = "🟢" if item["change_pct"] >= 0 else "🔴"
                    movers.append(f"- {arrow} {item['name']} ({item['change_pct']:+.1f}%)")
        movers_str = "\n".join(movers) if movers else ""

        prompt = (
            f"Evening wrap-up for Shay (now {now.strftime('%H:%M')}):\n\n"
            f"What happened today ({len(todays)} interactions):\n{today_summary}\n\n"
            f"Tomorrow's calendar:\n{tomorrow_str}\n\n"
            f"Open follow-ups:\n{followup_str}\n\n"
        )
        if movers_str:
            prompt += f"Notable market moves today:\n{movers_str}\n\n"
        prompt += (
            "Write a short evening message — recap + preview of tomorrow.\n"
            "If there's something critical tomorrow, highlight it.\n"
            "If there are open follow-ups or commitments, mention the most important one.\n"
            "If there were notable market moves, include a quick note.\n"
            "Tone: calm, direct, like a friend syncing at end of day."
        )

        chat = await llm_call(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            timeout=15,

        )
        return chat.choices[0].message.content if chat else None

    except Exception as e:
        logger.error(f"Evening wrap-up error: {e}")
        return None
