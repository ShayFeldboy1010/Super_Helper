import asyncio
import logging
import json
from app.core.database import supabase
from app.services.google_svc import GoogleService
from app.services.search_service import web_search, format_search_results
from app.services.news_service import fetch_ai_news
from app.services.market_service import fetch_market_data, fetch_symbols, extract_tickers_from_query
from app.services.synergy_service import generate_synergy_insights
from app.services.memory_service import get_relevant_insights
from app.core.llm import llm_call
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY

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

    async def answer_query(self, query_text: str, context_needed: list[str], target_date: str = None, memory_context: str = "") -> str:
        context_data = []

        # 1. Fetch Calendar if explicitly needed
        if "calendar" in context_needed:
            events = await self.google.get_events_for_date(target_date)
            date_label = target_date if target_date else "today"
            context_data.append(f"📅 Events for {date_label}:\n" + "\n".join(events))

        # 2. Fetch Tasks if explicitly needed
        if "tasks" in context_needed:
            try:
                response = supabase.table("tasks").select("*").eq("user_id", self.user_id).eq("status", "pending").execute()
                tasks = response.data
                if tasks:
                    task_list = "\n".join([f"- {t['title']} (Due: {t.get('due_at')})" for t in tasks])
                    context_data.append(f"✅ Open tasks:\n{task_list}")
                else:
                    context_data.append("✅ No open tasks.")
            except Exception as e:
                logger.error(f"Error fetching tasks: {e}")

        # 3. Fetch Notes / Archive search
        if "notes" in context_needed or "archive" in context_needed:
            try:
                from app.services.archive_service import search_archive
                notes = await search_archive(self.user_id, query_text, limit=10)
                if notes:
                    note_list = "\n".join([f"- {n['content']} (Tags: {n['tags']})" for n in notes])
                    context_data.append(f"📝 Saved notes:\n{note_list}")
                else:
                    context_data.append("📝 No matching notes found in archive.")
            except Exception as e:
                logger.error(f"Error fetching notes: {e}")

        # 4. Fetch Emails
        if "email" in context_needed:
            try:
                emails = await self.google.get_recent_emails(max_results=5)
                if emails:
                    email_lines = []
                    for e in emails:
                        email_lines.append(f"- From: {e['from']} | Subject: {e['subject']}\n  {e['snippet'][:100]}")
                    context_data.append(f"📧 Recent emails:\n" + "\n".join(email_lines))
                else:
                    context_data.append("📧 No recent emails.")
            except Exception as e:
                logger.error(f"Error fetching emails: {e}")

        # 5. Web Search
        if "web" in context_needed:
            try:
                results = await web_search(query_text, max_results=5)
                if results:
                    context_data.append(f"🌐 Search results:\n{format_search_results(results)}")
            except Exception as e:
                logger.error(f"Error in web search: {e}")

        # 6. AI News (RSS feeds — reliable, no API key needed)
        if "news" in context_needed:
            try:
                news_items = await fetch_ai_news(max_items=5, hours_back=24)
                if news_items:
                    lines = []
                    for n in news_items:
                        summary = f"\n  {n['summary'][:120]}" if n.get("summary") else ""
                        lines.append(f"- {n['title']} ({n['source']}){summary}")
                    context_data.append(f"🤖 AI News (live):\n" + "\n".join(lines))
                else:
                    context_data.append("🤖 No recent AI news found.")
            except Exception as e:
                logger.error(f"Error fetching AI news: {e}")

        # 7. Market Data (Yahoo Finance — real-time prices)
        if "market" in context_needed:
            try:
                # Detect specific tickers the user asked about
                specific_tickers = extract_tickers_from_query(query_text)
                # Remove tickers already in the default watchlist to avoid duplicates
                default_tickers = {"NVDA", "MSFT", "GOOGL", "META", "AAPL"}
                extra_tickers = [t for t in specific_tickers if t not in default_tickers]

                # Fetch default watchlist + any extra tickers in parallel
                fetches = [fetch_market_data()]
                if extra_tickers:
                    fetches.append(fetch_symbols(extra_tickers))

                results = await asyncio.gather(*fetches, return_exceptions=True)
                market = results[0] if isinstance(results[0], dict) else {"indices": [], "tickers": []}
                extra_data = results[1] if len(results) > 1 and isinstance(results[1], list) else []

                lines = []
                # Show specifically requested tickers first
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
                    context_data.append(f"📊 Market Data (live):\n" + "\n".join(lines))
                else:
                    context_data.append("📊 No market data available.")
            except Exception as e:
                logger.error(f"Error fetching market data: {e}")

        # 8. Synergy — AI-market opportunity analysis
        if "synergy" in context_needed:
            try:
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
                context_data.append(f"💡 Market-AI Synergy:\n{synergy}")
            except Exception as e:
                logger.error(f"Error in synergy analysis: {e}")

        # 9. Recent conversation for continuity
        recent_convo = await self._get_recent_conversation(limit=5)

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

        # Enable thinking for complex queries (ones that fetched external data)
        use_thinking = bool(context_data)

        chat_completion = await llm_call(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.7,
            timeout=8,
            thinking=use_thinking,
        )
        if not chat_completion:
            return "Something went wrong. Try again."
        return chat_completion.choices[0].message.content
