import asyncio
import logging
from datetime import datetime

from groq import AsyncGroq

from app.core.config import settings
from app.services.google_svc import GoogleService
from app.services.task_service import get_pending_tasks
from app.services.news_service import fetch_ai_news
from app.services.market_service import fetch_market_data

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)


def detect_conflicts(events: list[dict]) -> list[str]:
    """Find overlapping calendar events."""
    timed = []
    for ev in events:
        if "T" in ev.get("start", "") and "T" in ev.get("end", ""):
            try:
                start = datetime.fromisoformat(ev["start"])
                end = datetime.fromisoformat(ev["end"])
                timed.append({"summary": ev["summary"], "start": start, "end": end})
            except ValueError:
                continue

    conflicts = []
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            a, b = timed[i], timed[j]
            if a["start"] < b["end"] and b["start"] < a["end"]:
                conflicts.append(
                    f"⚠️ חפיפה: \"{a['summary']}\" ({a['start'].strftime('%H:%M')}-{a['end'].strftime('%H:%M')}) "
                    f"עם \"{b['summary']}\" ({b['start'].strftime('%H:%M')}-{b['end'].strftime('%H:%M')})"
                )
    return conflicts


def _format_events_context(events: list[dict]) -> str:
    if not events:
        return "אין אירועים היום."
    lines = []
    for ev in events:
        start = ev.get("start", "")
        time_str = start
        if "T" in start:
            try:
                time_str = datetime.fromisoformat(start).strftime("%H:%M")
            except ValueError:
                pass
        loc = f" [{ev.get('location')}]" if ev.get("location") else ""
        lines.append(f"• {time_str} - {ev['summary']}{loc}")
    return "\n".join(lines)


def _format_news_context(news: list[dict]) -> str:
    if not news:
        return "אין חדשות AI חדשות."
    lines = [f"• {n['title']} ({n['source']})" for n in news[:5]]
    return "\n".join(lines)


def _format_market_context(market: dict) -> str:
    lines = []
    for idx in market.get("indices", []):
        arrow = "🟢" if idx["change_pct"] >= 0 else "🔴"
        lines.append(f"{arrow} {idx['name']}: {idx['price']:,.0f} ({idx['change_pct']:+.1f}%)")
    for t in market.get("tickers", []):
        arrow = "🟢" if t["change_pct"] >= 0 else "🔴"
        lines.append(f"{arrow} {t['name']}: ${t['price']:,.2f} ({t['change_pct']:+.1f}%)")
    return "\n".join(lines) if lines else "אין נתוני שוק."


def _format_tasks_context(tasks: list[dict]) -> str:
    if not tasks:
        return "אין משימות פתוחות."
    lines = []
    for t in tasks[:7]:
        due = f" (עד: {t.get('due_at', 'ללא')})" if t.get("due_at") else ""
        lines.append(f"• {t['title']}{due}")
    return "\n".join(lines)


async def generate_morning_briefing(user_id: int) -> str:
    """Orchestrate full morning briefing with parallel data fetch."""
    google = GoogleService(user_id)
    await google.authenticate()

    # Parallel data fetch
    events_task = google.get_todays_events_detailed()
    emails_task = google.get_recent_emails(max_results=5)
    news_task = fetch_ai_news(max_items=5, hours_back=24)
    market_task = fetch_market_data()
    tasks_task = get_pending_tasks(user_id, limit=7)

    events, emails, news, market, tasks = await asyncio.gather(
        events_task, emails_task, news_task, market_task, tasks_task,
        return_exceptions=True,
    )

    # Handle exceptions gracefully
    if isinstance(events, Exception):
        logger.error(f"Events fetch failed: {events}")
        events = []
    if isinstance(emails, Exception):
        logger.error(f"Emails fetch failed: {emails}")
        emails = []
    if isinstance(news, Exception):
        logger.error(f"News fetch failed: {news}")
        news = []
    if isinstance(market, Exception):
        logger.error(f"Market fetch failed: {market}")
        market = {"indices": [], "tickers": []}
    if isinstance(tasks, Exception):
        logger.error(f"Tasks fetch failed: {tasks}")
        tasks = []

    # Detect calendar conflicts
    conflicts = detect_conflicts(events) if events else []
    conflicts_str = "\n".join(conflicts) if conflicts else "אין חפיפות."

    # Format email context
    email_lines = []
    for e in (emails or []):
        email_lines.append(f"• מאת: {e['from']} | נושא: {e['subject']}")
    emails_str = "\n".join(email_lines) if email_lines else "אין אימיילים חדשים."

    # Build context for LLM
    context = (
        f"📅 אירועים היום:\n{_format_events_context(events)}\n\n"
        f"⚠️ חפיפות:\n{conflicts_str}\n\n"
        f"📧 אימיילים אחרונים:\n{emails_str}\n\n"
        f"🤖 חדשות AI:\n{_format_news_context(news)}\n\n"
        f"📊 שוק:\n{_format_market_context(market)}\n\n"
        f"✅ משימות פתוחות:\n{_format_tasks_context(tasks)}"
    )

    system_prompt = (
        "אתה ראש מטה אישי (Chief of Staff). תפקידך: לתת תדריך בוקר חד וממוקד.\n"
        "כתוב בעברית. פורמט BLUF — שורה תחתונה קודם.\n"
        "השתמש בבולטים, לא פסקאות.\n"
        "הודעת טלגרם — תמציתי, ללא מילות מילוי.\n\n"
        "בנה את התדריך בסעיפים הבאים (השתמש באימוג'ים ככותרות):\n"
        "1. 📋 אג'נדה טקטית — לוח זמנים, חפיפות, אימיילים קריטיים\n"
        "2. 🤖 מודיעין AI — 2-3 התפתחויות מפתח\n"
        "3. 📊 אלפא שוק — מדדים ומניות בולטות\n"
        "4. 🎯 מהלך אקטיבי — רעיון אחד לפרויקט/מוצר בהתבסס על החדשות\n"
        "5. ✅ משימות חכמות — 2-3 משימות מומלצות מהמשימות הפתוחות\n\n"
        "אם אין מידע לסעיף מסוים, דלג עליו. אל תמציא מידע."
    )

    try:
        chat_completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"הנה הנתונים לתדריך הבוקר:\n\n{context}"},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            temperature=0.7,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Briefing LLM error: {e}")
        # Fallback: return raw formatted data
        return (
            f"☀️ *תדריך בוקר*\n\n"
            f"📅 *יומן:*\n{_format_events_context(events)}\n\n"
            f"{''.join(c + chr(10) for c in conflicts)}"
            f"📧 *אימיילים:*\n{emails_str}\n\n"
            f"✅ *משימות:*\n{_format_tasks_context(tasks)}"
        )
