import asyncio
import logging
from datetime import datetime

from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.services.google_svc import GoogleService
from app.services.task_service import get_pending_tasks
from app.services.news_service import fetch_ai_news
from app.services.market_service import fetch_market_data
from app.services.synergy_service import generate_synergy_insights
from app.services.memory_service import get_relevant_insights

logger = logging.getLogger(__name__)


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
    conflicts_str = "\n".join(conflicts) if conflicts else "No conflicts."

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
        f"📧 Recent Emails:\n{emails_str}\n\n"
        f"🤖 AI News:\n{_format_news_context(news)}\n\n"
        f"📊 Market:\n{_format_market_context(market)}\n\n"
        f"💡 Market-AI Synergy:\n{synergy_insights}\n\n"
        f"✅ Open Tasks:\n{_format_tasks_context(tasks)}"
    )

    briefing_instructions = (
        "\n\n=== Morning Briefing Instructions ===\n"
        "Build a sharp morning briefing for Telegram. Make it SCANNABLE.\n\n"
        "Sections (emoji header on its own line, then bullet points):\n"
        "1. 📋 Today's Agenda\n"
        "2. 🤖 AI Intel\n"
        "3. 📊 Market\n"
        "4. 💡 Synergy\n"
        "5. ✅ Tasks\n\n"
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
        timeout=8,
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
