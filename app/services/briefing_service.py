"""Morning briefing orchestrator — aggregates calendar, tasks, news, market data, and emails."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.services import igpt_service as igpt
from app.services.google_svc import GoogleService
from app.services.market_service import fetch_market_data
from app.services.memory_service import get_pending_follow_ups, get_relevant_insights
from app.services.news_service import fetch_ai_news
from app.services.synergy_service import generate_synergy_insights

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Jerusalem")


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
                    f"⚠️ Conflict: \"{a['summary']}\" ({a['start'].strftime('%H:%M')}-{a['end'].strftime('%H:%M')}) "
                    f"overlaps with \"{b['summary']}\" ({b['start'].strftime('%H:%M')}-{b['end'].strftime('%H:%M')})"
                )
    return conflicts


def _format_events_context(events: list[dict]) -> str:
    """Format calendar events as bullet points."""
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
    """Format news items as bullet points."""
    if not news:
        return "אין חדשות AI חדשות."
    lines = [f"• {n['title']} ({n['source']})" for n in news[:5]]
    return "\n".join(lines)


def _format_market_context(market: dict) -> str:
    """Format market data with directional indicators."""
    lines = []
    for idx in market.get("indices", []):
        arrow = "🟢" if idx["change_pct"] >= 0 else "🔴"
        lines.append(f"{arrow} {idx['name']}: {idx['price']:,.0f} ({idx['change_pct']:+.1f}%)")
    for t in market.get("tickers", []):
        arrow = "🟢" if t["change_pct"] >= 0 else "🔴"
        lines.append(f"{arrow} {t['name']}: ${t['price']:,.2f} ({t['change_pct']:+.1f}%)")
    return "\n".join(lines) if lines else "אין נתוני שוק."


def _compute_day_profile(events: list[dict]) -> str:
    """Return context-specific instructions based on day of week and schedule density."""
    now = datetime.now(TZ)
    day_name = now.strftime("%A")  # e.g. "Sunday"
    day_num = now.weekday()  # 0=Mon, 6=Sun

    # Count timed events
    timed_count = sum(1 for ev in events if "T" in ev.get("start", ""))

    parts = [f"היום {day_name}."]

    # Day-of-week context
    if day_num == 6:  # Sunday (Israel work week start)
        parts.append("תחילת שבוע: התמקד בהגדרת עדיפויות לשבוע. ציין 2-3 דברים עיקריים להשבוע.")
    elif day_num == 4:  # Friday
        parts.append("סוף שבוע עבודה: סגור קצוות לפני סופ\"ש. סמן דברים שלא יכולים לחכות ליום ראשון.")
    elif day_num == 5:  # Saturday
        parts.append("שבת: קח את זה קל. רק דברים באמת דחופים.")

    # Meeting density
    if timed_count >= 4:
        parts.append(f"יום פגישות צפוף ({timed_count} פגישות): סמן חלונות לעבודה מרוכזת והזהר מפגישות רצופות.")
    elif timed_count == 0:
        parts.append("בלי פגישות: הזדמנות לעבודה עמוקה.")

    return " ".join(parts)


def _analyze_day_structure(events: list[dict]) -> str:
    """Compute free slots and back-to-back warnings. Pure Python, no LLM."""
    lines = []

    # Parse timed events into (start, end, summary)
    timed = []
    for ev in events:
        if "T" in ev.get("start", "") and "T" in ev.get("end", ""):
            try:
                start = datetime.fromisoformat(ev["start"])
                end = datetime.fromisoformat(ev["end"])
                timed.append((start, end, ev.get("summary", "?")))
            except ValueError:
                continue
    timed.sort(key=lambda x: x[0])

    # Find free slots (gaps >= 45 min between events)
    free_slots = []
    for i in range(len(timed) - 1):
        gap_start = timed[i][1]
        gap_end = timed[i + 1][0]
        gap_minutes = (gap_end - gap_start).total_seconds() / 60
        if gap_minutes >= 45:
            free_slots.append(
                f"  {gap_start.strftime('%H:%M')}-{gap_end.strftime('%H:%M')} ({int(gap_minutes)}min free)"
            )

    if free_slots:
        lines.append("Free slots:\n" + "\n".join(free_slots))

    # Back-to-back warnings (< 10 min gap)
    back_to_back = []
    for i in range(len(timed) - 1):
        gap_minutes = (timed[i + 1][0] - timed[i][1]).total_seconds() / 60
        if gap_minutes < 10:
            back_to_back.append(
                f"  ⚠️ {timed[i][2]} → {timed[i+1][2]} (only {int(gap_minutes)}min gap)"
            )

    if back_to_back:
        lines.append("Back-to-back warnings:\n" + "\n".join(back_to_back))

    return "\n\n".join(lines)


async def generate_morning_briefing(user_id: int) -> str:
    """Orchestrate full morning briefing with parallel data fetch."""
    google = GoogleService(user_id)
    await google.authenticate()

    # Parallel data fetch — use iGPT for emails when available
    events_task = google.get_todays_events_detailed()
    if settings.igpt_enabled:
        emails_task = igpt.ask(
            "Summarize my inbox highlights and action items from the last 24 hours"
        )
    else:
        emails_task = google.get_recent_emails(max_results=5)
    news_task = fetch_ai_news(max_items=5, hours_back=24)
    market_task = fetch_market_data()
    followups_task = get_pending_follow_ups(user_id, limit=5)

    events, emails, news, market, follow_ups = await asyncio.gather(
        events_task, emails_task, news_task, market_task, followups_task,
        return_exceptions=True,
    )

    # Handle exceptions gracefully
    if isinstance(events, Exception):
        logger.error(f"Events fetch failed: {events}")
        events = []
    if isinstance(emails, Exception):
        logger.error(f"Emails fetch failed: {emails}")
        emails = [] if not settings.igpt_enabled else None
    if isinstance(news, Exception):
        logger.error(f"News fetch failed: {news}")
        news = []
    if isinstance(market, Exception):
        logger.error(f"Market fetch failed: {market}")
        market = {"indices": [], "tickers": []}
    if isinstance(follow_ups, Exception):
        logger.error(f"Follow-ups fetch failed: {follow_ups}")
        follow_ups = []

    # Detect calendar conflicts
    conflicts = detect_conflicts(events) if events else []
    conflicts_str = "\n".join(conflicts) if conflicts else "אין התנגשויות."

    # Compute day profile and structure analysis
    day_profile = _compute_day_profile(events if isinstance(events, list) else [])
    day_structure = _analyze_day_structure(events if isinstance(events, list) else [])

    # Format email context — iGPT returns a string, Gmail returns a list (or None if auth failed)
    # If iGPT says it can't access, treat as empty so Gmail fallback formatting kicks in
    if isinstance(emails, str):
        no_access_phrases = ["have access", "don't have access", "cannot access",
                             "אין לי גישה", "אין גישה", "לא יכול לגשת"]
        if any(phrase in emails.lower() for phrase in no_access_phrases):
            emails = None
    if isinstance(emails, str) and emails:
        emails_str = emails  # iGPT semantic summary
    elif isinstance(emails, list) and emails:
        email_lines = [f"• From: {e['from']} | Subject: {e['subject']}" for e in emails]
        emails_str = "\n".join(email_lines)
    elif emails is None:
        emails_str = "⚠️ Gmail לא מחובר — יש לחבר דרך /auth."
    else:
        emails_str = "אין מיילים חדשים."

    # Generate synergy insights (uses already-fetched news + market)
    try:
        user_insights = await get_relevant_insights(user_id, action_type="query")
    except Exception as e:
        logger.error(f"User insights fetch failed: {e}")
        user_insights = ""

    try:
        synergy_insights = await generate_synergy_insights(
            news if isinstance(news, list) else [],
            market if isinstance(market, dict) else {"indices": [], "tickers": []},
            user_insights,
        )
    except Exception as e:
        logger.error(f"Synergy generation failed: {e}")
        synergy_insights = ""

    # Build context for LLM
    context = (
        f"📅 Today's Events:\n{_format_events_context(events)}\n\n"
        f"⚠️ Conflicts:\n{conflicts_str}\n\n"
    )
    if day_structure:
        context += f"🗓 Day Structure Analysis:\n{day_structure}\n\n"
    context += (
        f"📧 Recent Emails:\n{emails_str}\n\n"
        f"🤖 AI News:\n{_format_news_context(news)}\n\n"
        f"📊 Market:\n{_format_market_context(market)}\n\n"
        f"💡 Market-AI Synergy:\n{synergy_insights}"
    )

    # Add follow-ups to context if any
    if follow_ups:
        fu_lines = []
        for fu in follow_ups:
            due = f" (due: {fu['due_at'][:10]})" if fu.get("due_at") else ""
            fu_lines.append(f"• {fu['commitment']}{due}")
        context += "\n\n🔄 Open Follow-ups:\n" + "\n".join(fu_lines)

    briefing_instructions = (
        "\n\n=== הוראות בריפינג בוקר ===\n"
        f"הקשר היום: {day_profile}\n\n"
        "בנה בריפינג בוקר חד לטלגרם. שיהיה סריק ונקי.\n\n"
        "סעיפים (אמוג'י ככותרת בשורה נפרדת, אחריו נקודות):\n"
        "1. 📋 סדר יום (כולל תזכורות מהיומן)\n"
        "1b. 🗓 תוכנית יום (רק אם יש בעיות/פערים — תציע איך לבנות את היום)\n"
        "2. 🤖 חדשות AI\n"
        "3. 📊 שוק\n"
        "4. 💡 סינרגיה\n"
        "5. 🔄 המשכים (תזכיר התחייבויות פתוחות משיחות קודמות — רק אם יש)\n\n"
        "כללי פורמט (קפדני):\n"
        "- כל כותרת סעיף בשורה אחת עם אמוג'י, אחריה שורה ריקה\n"
        "- נקודות קצרות (שורה אחת כל אחת), תתחיל כל אחת עם חץ או מקף\n"
        "- מקסימום 1-2 משפטים לנקודה. בלי פסקאות ארוכות. אף פעם.\n"
        "- מספרים/טיקרים בשורה נפרדת: 🟢 NVDA $190.50 (+0.8%)\n"
        "- שורה ריקה בין סעיפים לנשימה\n"
        "- בלי markdown (בלי **, בלי ##, בלי __)\n"
        "- תדבר כמו חבר חד, לא כמו קריין חדשות\n"
        "- תובנות סינרגיה כבר מנותחות. תציג כנקודות קצרות, בלי לנתח מחדש.\n"
        "- אם אין מידע לסעיף, תדלג עליו לגמרי\n"
    )
    system_prompt = CHIEF_OF_STAFF_IDENTITY + briefing_instructions

    chat_completion = await llm_call(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Here's the data for the morning briefing:\n\n{context}"},
        ],
        temperature=0.7,
        timeout=30,

    )
    if chat_completion:
        return chat_completion.choices[0].message.content
    # Fallback: return raw formatted data
    return (
        f"בריפינג בוקר\n\n"
        f"📅 יומן:\n{_format_events_context(events)}\n\n"
        f"{''.join(c + chr(10) for c in conflicts)}"
        f"📧 מיילים:\n{emails_str}"
    )


MEETING_PREP_PROMPT = (
    "צור תקציר הכנה לפגישה. מקסימום 10 שורות. כלול:\n"
    "- עם מי הפגישה (שמות/תפקידים אם אפשר להסיק)\n"
    "- הקשר אחרון ממיילים או הערות\n"
    "- נושאים שצפויים לעלות\n"
    "- 1-2 דברים להכין או לזכור\n\n"
    "פורמט: נקודות נקיות, בלי markdown. תתחיל עם שם הפגישה והשעה.\n"
    "אם אין הקשר מועיל מעבר לשם הפגישה, תגיד את זה בקצרה — אל תמציא."
)


async def generate_meeting_prep(user_id: int) -> list[str]:
    """Generate prep briefs for upcoming meetings (within 45 min). Returns list of messages."""
    from app.core.cache import cache_get, cache_set
    from app.services.archive_service import search_archive

    google = GoogleService(user_id)
    await google.authenticate()

    upcoming = await google.get_upcoming_events_detailed(minutes_ahead=45)
    if not upcoming:
        return []

    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    messages = []
    for event in upcoming[:1]:  # Process max 1 per invocation to stay under 10s
        event_id = event.get("event_id", "")

        # Skip if already prepped (dedup)
        if cache_get(f"meeting_prep:{event_id}"):
            continue

        # Skip meetings starting in less than 10 min (too late for prep)
        start_dt = datetime.fromisoformat(event["start"])
        if (start_dt - now).total_seconds() / 60 < 10:
            continue

        # Skip recurring meetings with no attendees (routine standups)
        if event.get("recurring_event_id") and not event.get("attendees"):
            continue

        attendees = event.get("attendees", [])

        # Fetch email history from attendees (parallel, max 3 attendees, 2 emails each)
        email_tasks = []
        for att in attendees[:3]:
            email_tasks.append(google.search_emails_from_sender(att["email"], max_results=2))

        # Fetch archive notes matching meeting title
        archive_task = search_archive(user_id, event.get("summary", ""), limit=5)

        all_results = await asyncio.gather(*email_tasks, archive_task, return_exceptions=True)

        # Parse email results
        email_context_lines = []
        for i, result in enumerate(all_results[:-1]):
            if isinstance(result, Exception) or not result:
                continue
            att_name = attendees[i]["name"] if i < len(attendees) else "Unknown"
            for email in result:
                email_context_lines.append(
                    f"  Email from {att_name}: {email['subject']} — {email.get('snippet', '')[:100]}"
                )

        # Parse archive results
        archive_result = all_results[-1]
        archive_lines = []
        if not isinstance(archive_result, Exception) and archive_result:
            for note in archive_result[:3]:
                archive_lines.append(f"  Note: {note.get('content', '')[:120]}")

        # Build context for LLM
        start_time = event.get("start", "")
        if "T" in start_time:
            try:
                start_time = datetime.fromisoformat(start_time).strftime("%H:%M")
            except ValueError:
                pass

        attendee_str = ", ".join(a["name"] for a in attendees) if attendees else "No attendees listed"
        context = (
            f"Meeting: {event.get('summary', '?')}\n"
            f"Time: {start_time}\n"
            f"Attendees: {attendee_str}\n"
        )
        if event.get("location"):
            context += f"Location: {event['location']}\n"
        if event.get("description"):
            context += f"Description: {event['description'][:200]}\n"
        if email_context_lines:
            context += "\nRecent emails with attendees:\n" + "\n".join(email_context_lines[:6])
        if archive_lines:
            context += "\nRelevant notes:\n" + "\n".join(archive_lines)

        # LLM call
        chat = await llm_call(
            messages=[
                {"role": "system", "content": CHIEF_OF_STAFF_IDENTITY + "\n\n" + MEETING_PREP_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.5,
            timeout=15,
    
        )

        if chat:
            prep_text = chat.choices[0].message.content
            messages.append(f"📋 הכנה לפגישה\n\n{prep_text}")
        else:
            messages.append(
                f"📋 הכנה לפגישה: {event.get('summary', '?')} ב-{start_time}\n"
                f"משתתפים: {attendee_str}"
            )

        # Mark as prepped
        cache_set(f"meeting_prep:{event_id}", True, 3600)

    return messages
