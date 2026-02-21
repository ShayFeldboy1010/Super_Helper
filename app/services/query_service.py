"""Context-aware query answering -- fetches relevant data sources in parallel and synthesizes via LLM."""

import asyncio
import logging

from app.core.config import settings
from app.core.database import supabase
from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.services import igpt_service as igpt
from app.services.google_svc import GoogleService
from app.services.market_service import extract_tickers_from_query, fetch_market_data, fetch_symbols
from app.services.memory_service import get_relevant_insights
from app.services.news_service import fetch_ai_news
from app.services.search_service import format_search_results, web_search
from app.services.synergy_service import generate_synergy_insights

logger = logging.getLogger(__name__)


class QueryService:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.google = GoogleService(user_id)

    async def _get_recent_conversation(self, limit: int = 5) -> str:
        """Fetch recent interactions for conversational continuity."""
        try:
            resp = (
                supabase.table("interaction_log")
                .select("user_message, bot_response, action_type")
                .eq("user_id", self.user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            if not resp.data:
                return ""

            lines = []
            for ix in reversed(resp.data):  # chronological order
                lines.append(f"Shay: {ix['user_message'][:100]}")
                lines.append(f"You: {ix['bot_response'][:150]}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error fetching recent conversation: {e}")
            return ""

    async def answer_query(self, query_text: str, context_needed: list[str], target_date: str = None, memory_context: str = "", archive_since: str = None) -> str:
        """Fetch context from requested sources in parallel and answer the query via LLM."""
        # --- Parallel context fetching ---
        async def _fetch_calendar():
            events = await self.google.get_events_for_date(target_date)
            date_label = target_date if target_date else "today"
            return f"📅 Events for {date_label}:\n" + "\n".join(events)

        async def _fetch_tasks():
            response = supabase.table("tasks").select("*").eq("user_id", self.user_id).eq("status", "pending").execute()
            tasks = response.data
            if tasks:
                task_list = "\n".join([f"- {t['title']} (Due: {t.get('due_at')}, Effort: {t.get('effort', '-')})" for t in tasks])
                return f"✅ Open tasks:\n{task_list}"
            return "✅ No open tasks."

        async def _fetch_archive():
            from datetime import datetime as _dt
            from datetime import timedelta as _td
            from zoneinfo import ZoneInfo as _ZI

            from app.services.archive_service import search_archive
            # Compute since date from archive_since
            since_date = None
            if archive_since:
                now = _dt.now(_ZI("Asia/Jerusalem"))
                if archive_since == "today":
                    since_date = now.strftime("%Y-%m-%d")
                elif archive_since == "week":
                    since_date = (now - _td(days=7)).strftime("%Y-%m-%d")
                elif archive_since == "month":
                    since_date = (now - _td(days=30)).strftime("%Y-%m-%d")
                elif archive_since == "year":
                    since_date = (now - _td(days=365)).strftime("%Y-%m-%d")
            notes = await search_archive(self.user_id, query_text, limit=10, since=since_date)
            if notes:
                note_list = "\n".join([f"- {n['content'][:150]} (Tags: {n['tags']})" for n in notes])
                return f"📝 Saved notes:\n{note_list}"
            return "📝 No matching notes found in archive."

        async def _fetch_email_igpt():
            answer = await igpt.ask(query_text)
            if answer and "have access" not in answer.lower():
                return f"📧 Email Intelligence (iGPT):\n{answer}"
            return await _fetch_email_gmail()

        async def _fetch_email_gmail():
            emails = await self.google.get_recent_emails(max_results=5)
            if emails:
                email_lines = [f"- From: {e['from']} | Subject: {e['subject']}\n  {e['snippet'][:100]}" for e in emails]
                return "📧 Recent emails:\n" + "\n".join(email_lines)
            return "📧 No recent emails."

        async def _fetch_web():
            results = await web_search(query_text, max_results=5)
            if results:
                return f"🌐 Search results:\n{format_search_results(results)}"
            return None

        async def _fetch_news():
            news_items = await fetch_ai_news(max_items=5, hours_back=24)
            if news_items:
                lines = []
                for n in news_items:
                    summary = f"\n  {n['summary'][:120]}" if n.get("summary") else ""
                    lines.append(f"- {n['title']} ({n['source']}){summary}")
                return "🤖 AI News (live):\n" + "\n".join(lines)
            return "🤖 No recent AI news found."

        async def _fetch_market():
            specific_tickers = extract_tickers_from_query(query_text)
            default_tickers = {"NVDA", "MSFT", "GOOGL", "META", "AAPL"}
            extra_tickers = [t for t in specific_tickers if t not in default_tickers]
            fetches = [fetch_market_data()]
            if extra_tickers:
                fetches.append(fetch_symbols(extra_tickers))
            results = await asyncio.gather(*fetches, return_exceptions=True)
            market = results[0] if isinstance(results[0], dict) else {"indices": [], "tickers": []}
            extra_data = results[1] if len(results) > 1 and isinstance(results[1], list) else []
            lines = []
            for t in extra_data:
                arrow = "🟢" if t["change_pct"] >= 0 else "🔴"
                lines.append(f"{arrow} {t['name']}: ${t['price']:,.2f} ({t['change_pct']:+.1f}%)")
            for idx in market.get("indices", []):
                arrow = "🟢" if idx["change_pct"] >= 0 else "🔴"
                lines.append(f"{arrow} {idx['name']}: {idx['price']:,.0f} ({idx['change_pct']:+.1f}%)")
            for t in market.get("tickers", []):
                arrow = "🟢" if t["change_pct"] >= 0 else "🔴"
                lines.append(f"{arrow} {t['name']}: ${t['price']:,.2f} ({t['change_pct']:+.1f}%)")
            if lines:
                return "📊 Market Data (live):\n" + "\n".join(lines)
            return "📊 No market data available."

        async def _fetch_synergy():
            news, market = await asyncio.gather(
                fetch_ai_news(max_items=5, hours_back=24),
                fetch_market_data(),
                return_exceptions=True,
            )
            if isinstance(news, Exception):
                news = []
            if isinstance(market, Exception):
                market = {"indices": [], "tickers": []}
            user_insights = await get_relevant_insights(self.user_id, action_type="query", query_text=query_text)
            synergy = await generate_synergy_insights(news, market, user_insights)
            return f"💡 Market-AI Synergy:\n{synergy}"

        # Build parallel fetch list based on context_needed
        fetch_map = {
            "calendar": _fetch_calendar,
            "tasks": _fetch_tasks,
            "archive": _fetch_archive,
            "notes": _fetch_archive,
            "email": _fetch_email_igpt if settings.igpt_enabled else _fetch_email_gmail,
            "web": _fetch_web,
            "news": _fetch_news,
            "market": _fetch_market,
            "synergy": _fetch_synergy,
        }

        # Deduplicate (notes/archive map to same func)
        seen_funcs = set()
        fetch_tasks = []
        fetch_labels = []
        for ctx in context_needed:
            func = fetch_map.get(ctx)
            if func and id(func) not in seen_funcs:
                seen_funcs.add(id(func))
                fetch_tasks.append(func())
                fetch_labels.append(ctx)

        # Always fetch conversation in parallel too
        fetch_tasks.append(self._get_recent_conversation(limit=5))
        fetch_labels.append("_conversation")

        # Run ALL fetches in parallel
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        context_data = []
        recent_convo = ""
        for label, result in zip(fetch_labels, results):
            if label == "_conversation":
                recent_convo = result if isinstance(result, str) else ""
            elif isinstance(result, Exception):
                logger.error(f"Error fetching {label}: {result}")
            elif result:
                context_data.append(result)

        # 10. Build system prompt with all context
        full_context = "\n\n".join(context_data) if context_data else ""

        system_prompt = CHIEF_OF_STAFF_IDENTITY

        if recent_convo:
            system_prompt += (
                "\n\n=== Recent Conversation (for continuity) ===\n"
                + recent_convo
            )

        if memory_context:
            system_prompt += (
                "\n\n=== What You Know About Shay ===\n"
                + memory_context
            )

        # 11. Build user message
        if full_context:
            user_content = f"Relevant data:\n{full_context}\n\nShay says: {query_text}"
        else:
            user_content = query_text

        chat_completion = await llm_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.7,
            timeout=15,

        )
        if not chat_completion:
            return "Something went wrong. Try again."
        return chat_completion.choices[0].message.content
