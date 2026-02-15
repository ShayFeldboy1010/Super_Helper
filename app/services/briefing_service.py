import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.services.google_svc import GoogleService
from app.services.task_service import get_pending_tasks
from app.services.news_service import fetch_ai_news
from app.services.market_service import fetch_market_data
from app.services.synergy_service import generate_synergy_insights
from app.services.memory_service import get_relevant_insights, get_pending_follow_ups

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
    if not events:
        return "No events today."
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
        return "No new AI news."
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
    return "\n".join(lines) if lines else "No market data available."


def _format_tasks_context(tasks: list[dict]) -> str:
    if not tasks:
        return "No open tasks."
    lines = []
    for t in tasks[:7]:
        due = f" (due: {t.get('due_at', 'none')})" if t.get("due_at") else ""
        lines.append(f"• {t['title']}{due}")
    return "\n".join(lines)


def _compute_day_profile(events: list[dict], tasks: list[dict]) -> str:
    """Return context-specific instructions based on day of week and schedule density."""
    now = datetime.now(TZ)
    day_name = now.strftime("%A")  # e.g. "Sunday"
    day_num = now.weekday()  # 0=Mon, 6=Sun

    # Count timed events
    timed_count = sum(1 for ev in events if "T" in ev.get("start", ""))

    # Check for overdue tasks
    overdue = []
    for t in tasks:
        if t.get("due_at"):
            try:
                due = datetime.fromisoformat(t["due_at"])
                if due < now:
                    overdue.append(t["title"])
            except (ValueError, TypeError):
                pass

    parts = [f"Today is {day_name}."]

    # Day-of-week context
    if day_num == 6:  # Sunday (Israel work week start)
        parts.append("WEEK KICKOFF: Focus on setting priorities for the week. Mention top 2-3 things to accomplish this week.")
    elif day_num == 4:  # Friday
        parts.append("WEEK WRAP-UP: Focus on closing loops before weekend. Flag anything that can't wait until Sunday.")
    elif day_num == 5:  # Saturday
        parts.append("WEEKEND: Keep it light. Only mention truly urgent items.")

    # Meeting density
    if timed_count >= 4:
        parts.append(f"HEAVY MEETING DAY ({timed_count} meetings): Flag gaps for focused work and warn about back-to-back meetings.")
    elif timed_count == 0:
        parts.append("NO MEETINGS: Deep work opportunity. Suggest tackling the highest-priority task.")

    # Overdue tasks
    if overdue:
        parts.append(f"OVERDUE ALERT: {len(overdue)} overdue task(s) — flag prominently: {', '.join(overdue[:3])}")

    return " ".join(parts)


def _analyze_day_structure(events: list[dict], tasks: list[dict]) -> str:
    """Compute free slots, back-to-back warnings, and unscheduled priorities. Pure Python, no LLM."""
    now = datetime.now(TZ)
    today = now.date()
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

    # Tasks due today
    due_today = []
    for t in tasks:
        if t.get("due_at"):
            try:
                due = datetime.fromisoformat(t["due_at"])
                if due.date() == today:
                    due_today.append(f"  • {t['title']} (due {due.strftime('%H:%M')})")
            except (ValueError, TypeError):
                pass

    if due_today:
        lines.append("Tasks due today:\n" + "\n".join(due_today))

    # Unscheduled high-priority tasks (priority >= 2, no due date)
    unscheduled_hp = [
        f"  • {t['title']} (priority {t.get('priority', 0)})"
        for t in tasks
        if t.get("priority", 0) >= 2 and not t.get("due_at")
    ]

    if unscheduled_hp:
        lines.append("Unscheduled high-priority tasks:\n" + "\n".join(unscheduled_hp))

    return "\n\n".join(lines)


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
    followups_task = get_pending_follow_ups(user_id, limit=5)

    events, emails, news, market, tasks, follow_ups = await asyncio.gather(
        events_task, emails_task, news_task, market_task, tasks_task, followups_task,
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
    if isinstance(follow_ups, Exception):
        logger.error(f"Follow-ups fetch failed: {follow_ups}")
        follow_ups = []

    # Detect calendar conflicts
    conflicts = detect_conflicts(events) if events else []
    conflicts_str = "\n".join(conflicts) if conflicts else "No conflicts."

    # Compute day profile and structure analysis
    day_profile = _compute_day_profile(events if isinstance(events, list) else [], tasks if isinstance(tasks, list) else [])
    day_structure = _analyze_day_structure(events if isinstance(events, list) else [], tasks if isinstance(tasks, list) else [])

    # Format email context
    email_lines = []
    for e in (emails or []):
        email_lines.append(f"• From: {e['from']} | Subject: {e['subject']}")
    emails_str = "\n".join(email_lines) if email_lines else "No new emails."

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
        f"💡 Market-AI Synergy:\n{synergy_insights}\n\n"
        f"✅ Open Tasks:\n{_format_tasks_context(tasks)}"
    )

    # Add follow-ups to context if any
    if follow_ups:
        fu_lines = []
        for fu in follow_ups:
            due = f" (due: {fu['due_at'][:10]})" if fu.get("due_at") else ""
            fu_lines.append(f"• {fu['commitment']}{due}")
        context += f"\n\n🔄 Open Follow-ups:\n" + "\n".join(fu_lines)

    briefing_instructions = (
        "\n\n=== Morning Briefing Instructions ===\n"
        f"DAY CONTEXT: {day_profile}\n\n"
        "Build a sharp morning briefing for Telegram. Make it SCANNABLE.\n\n"
        "Sections (emoji header on its own line, then bullet points):\n"
        "1. 📋 Today's Agenda\n"
        "1b. 🗓 Day Plan (only if gaps/issues detected — suggest how to structure the day)\n"
        "2. 🤖 AI Intel\n"
        "3. 📊 Market\n"
        "4. 💡 Synergy\n"
        "5. ✅ Tasks\n"
        "6. 🔄 Follow-ups (remind about open commitments from past conversations — only if any exist)\n\n"
        "FORMATTING RULES (strict):\n"
        "- Each section header is ONE line with emoji, then a blank line\n"
        "- Use short bullet points (one line each), start each with a dot or arrow\n"
        "- MAX 1-2 sentences per bullet. No long paragraphs. Ever.\n"
        "- Numbers/tickers get their own line: 🟢 NVDA $190.50 (+0.8%)\n"
        "- Blank line between sections for breathing room\n"
        "- NO markdown (no **, no ##, no __)\n"
        "- Talk like a sharp friend, not a news anchor\n"
        "- Synergy insights are pre-analyzed. Present them as short bullets, don't re-analyze.\n"
        "- If no data for a section, skip it entirely\n\n"
        "GOOD example bullet:\n"
        "  → xAI announced Mars AI timeline. NVDA jumped 0.8% on compute demand signal.\n\n"
        "BAD example (too long):\n"
        "  xAI just went full sci-fi with their Mars timeline — this isn't just Musk being Musk, "
        "it's a signal that space-grade AI infrastructure is becoming investable...\n"
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
        f"Morning Briefing\n\n"
        f"📅 Calendar:\n{_format_events_context(events)}\n\n"
        f"{''.join(c + chr(10) for c in conflicts)}"
        f"📧 Emails:\n{emails_str}\n\n"
        f"✅ Tasks:\n{_format_tasks_context(tasks)}"
    )


MEETING_PREP_PROMPT = (
    "Generate a quick meeting prep brief. Max 10 lines. Include:\n"
    "- Who you're meeting (names/roles if inferable)\n"
    "- Recent context from emails or notes\n"
    "- Topics likely to come up\n"
    "- 1-2 things to prepare or keep in mind\n\n"
    "Format: clean bullets, no markdown. Start with the meeting title and time.\n"
    "If there's no useful context beyond the meeting title, say so briefly — don't fabricate."
)


async def generate_meeting_prep(user_id: int) -> list[str]:
    """Generate prep briefs for upcoming meetings (within 20 min). Returns list of messages."""
    from app.core.cache import cache_get, cache_set
    from app.services.archive_service import search_archive

    google = GoogleService(user_id)
    await google.authenticate()

    upcoming = await google.get_upcoming_events_detailed(minutes_ahead=20)
    if not upcoming:
        return []

    messages = []
    for event in upcoming[:1]:  # Process max 1 per invocation to stay under 10s
        event_id = event.get("event_id", "")

        # Skip if already prepped (dedup)
        if cache_get(f"meeting_prep:{event_id}"):
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
            context += f"\nRecent emails with attendees:\n" + "\n".join(email_context_lines[:6])
        if archive_lines:
            context += f"\nRelevant notes:\n" + "\n".join(archive_lines)

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
            messages.append(f"📋 Meeting Prep\n\n{prep_text}")
        else:
            # Fallback: raw context
            messages.append(
                f"📋 Meeting Prep: {event.get('summary', '?')} at {start_time}\n"
                f"Attendees: {attendee_str}"
            )

        # Mark as prepped
        cache_set(f"meeting_prep:{event_id}", True, 3600)

    return messages
