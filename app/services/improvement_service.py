"""Improvement service — analyzes scanned content via Gemini Flash to generate
self-improvement proposals for the bot."""

import json
import logging

from app.core.config import settings
from app.core.database import supabase
from app.core.llm import llm_call
from app.services.content_scanner_service import scan_all_sources

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are the self-improvement engine for an AI personal assistant Telegram bot.

The bot's current capabilities:
- Task management (create, complete, delete, edit, schedule, recurring tasks)
- Google Calendar integration (create events, check schedule, find free slots)
- Note-taking and URL knowledge archive (auto-summarize, tag, search)
- Morning briefing (calendar, tasks, news, market, emails)
- AI news aggregation from RSS feeds
- Stock market tracking and alerts
- Email monitoring via Gmail API
- Weather alerts
- Web search via Brave API
- Memory system (interaction logging, daily reflection, permanent insights)
- Voice message transcription
- Evening wrap-up and weekly review

Stack: Python, FastAPI, aiogram (Telegram), Supabase (Postgres), Gemini Flash LLM, \
Google Calendar/Gmail API, deployed on Render.

Below are content items discovered from tech blogs, Hacker News, Dev.to, GitHub, and Reddit.
For each item, evaluate whether it suggests a concrete improvement to this bot.

Return JSON (no markdown, no code fences):
{
  "proposals": [
    {
      "item_index": 0,
      "relevant": true,
      "relevance_score": 0.85,
      "title": "Short title (3-5 words max)",
      "description": "One sentence, max 15 words, plain language",
      "implementation_detail": "Detailed 3-5 sentence explanation: what files to change, what logic to add, what the expected behavior should be. Be specific about function names, endpoints, and data flow.",
      "proposal_type": "feature|optimization|integration|fix"
    }
  ]
}

DO NOT suggest any of the following — they are ALREADY implemented:
- Inline keyboards / confirmation buttons (we use text-based confirmation flow with pending_confirmations table)
- Email monitoring / Gmail integration (already built via GoogleService + Gmail API, including iGPT deep email intelligence)
- Calendar integration / scheduling (already built: create events, check schedule, find free slots, schedule tasks into calendar)
- Voice message support / transcription (already built via Gemini audio transcription)
- Task management features: create, complete, delete, edit, schedule, recurring tasks, duplicate detection, effort estimates — all exist
- Morning briefing / daily digest (already built: calendar + tasks + news + market + emails + weather)
- Evening wrap-up / weekly review (already built)
- Memory system / conversation context (already built: interaction logging, daily reflection, permanent insights)
- Web search (already built via Brave API)
- AI news aggregation (already built via RSS feeds)
- Stock market tracking and alerts (already built)
- Weather alerts (already built)
- Note-taking / URL archive with auto-summarize and tagging (already built)
- Self-improvement / code task system (already built — that's YOU)
- Multi-LLM fallback (already built: Gemini primary + Groq/Kimi K2 fallback)
- Hebrew language support (already the default UI language)

Rules:
- Only include items with relevance_score > 0.6
- proposal_type must be one of: feature, optimization, integration, fix
- description: short for display (one sentence, no jargon)
- implementation_detail: detailed enough for a coding agent to implement without ambiguity
- Skip items that are too vague or unrelated to this bot's domain
- Skip items that suggest features from the DO NOT list above
- Maximum 5 proposals per batch
"""


async def analyze_content_batch(items: list[dict]) -> list[dict]:
    """Send items to LLM in batches of 10, return proposals with score > 0.6."""
    if not items:
        return []

    all_proposals = []
    batch_size = 10

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        items_text = "\n\n".join(
            f"[{j}] {it['title']}\nSource: {it['source']} | {it['url']}\n{it.get('summary', '')}"
            for j, it in enumerate(batch)
        )

        resp = await llm_call(
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": f"Content items:\n\n{items_text}"},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
            timeout=20,
        )

        if not resp:
            logger.warning(f"LLM call failed for batch starting at {i}")
            continue

        raw = resp.choices[0].message.content
        try:
            data = json.loads(raw)
            for p in data.get("proposals", []):
                if p.get("relevance_score", 0) > 0.6 and p.get("relevant"):
                    idx = p.get("item_index", 0)
                    if 0 <= idx < len(batch):
                        p["_source_item"] = batch[idx]
                    all_proposals.append(p)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse LLM proposals: {e}")

    return all_proposals[:5]  # Cap at 5


async def store_proposals(proposals: list[dict], user_id: int) -> int:
    """Insert proposals into the improvement_proposals table. Returns count stored."""
    count = 0
    for p in proposals:
        source_item = p.get("_source_item", {})
        try:
            # Store implementation_detail in description field for Claude Code
            detail = p.get("implementation_detail") or p.get("description", "")
            supabase.table("improvement_proposals").insert({
                "user_id": user_id,
                "source": source_item.get("source", "unknown"),
                "source_url": source_item.get("url", ""),
                "title": p.get("title", ""),
                "description": detail,
                "proposal_type": p.get("proposal_type", "feature"),
                "relevance_score": p.get("relevance_score", 0.0),
                "status": "pending",
            }).execute()
            count += 1
        except Exception as e:
            logger.error(f"Failed to store proposal: {e}")
    return count


def format_proposals_message(proposals: list[dict]) -> str:
    """Format proposals into a Hebrew Telegram message."""
    if not proposals:
        return "לא מצאתי רעיונות לשיפור היום."

    type_emoji = {
        "feature": "🆕",
        "optimization": "⚡",
        "integration": "🔗",
        "fix": "🔧",
    }

    lines = [f"🧠 {len(proposals)} רעיונות לשיפור:\n"]
    for i, p in enumerate(proposals, 1):
        emoji = type_emoji.get(p.get("proposal_type", "feature"), "💡")
        title = p.get("title", "No title")
        # One short sentence from description
        desc = p.get("description", "").split(".")[0].strip()
        lines.append(f"{i}. {emoji} {title}\n   {desc}")

    lines.append("\napprove N / reject N")
    return "\n".join(lines)


async def run_self_improvement_scan(user_id: int | None = None) -> dict:
    """Full orchestrator: scan sources -> analyze -> store -> format message."""
    if user_id is None:
        user_id = settings.TELEGRAM_USER_ID

    # 1. Scan all content sources
    items = await scan_all_sources()
    if not items:
        return {
            "items_found": 0,
            "proposals": 0,
            "message": "לא נמצא תוכן חדש לניתוח היום.",
        }

    # 2. Analyze via LLM
    proposals = await analyze_content_batch(items)

    # 3. Store in DB
    stored = await store_proposals(proposals, user_id)

    # 4. Format message
    message = format_proposals_message(proposals)

    return {
        "items_found": len(items),
        "proposals": stored,
        "message": message,
    }
