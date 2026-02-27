"""LLM-powered intent classification -- routes natural language input to action handlers."""

import json
import logging
from datetime import datetime

from app.core.database import supabase
from app.core.llm import llm_call
from app.models.router_models import RouterResponse

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """You are a fast intent classifier. Military precision, zero waste.
Classify the user's Hebrew input into one of 5 categories and extract details.

Current Date/Time: {current_time}
Day of week: {current_day}

{conversation_context}

Categories & Schemas:

1. **task**: Create a reminder — goes straight to Google Calendar.
Hebrew example - with date and time:
User: "תזכיר לי לקנות חלב מחר ב-10"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Create reminder: buy milk"}},
  "task": {{"title": "לקנות חלב", "due_date": "2026-02-17 10:00:00", "time": null}}
}}
Hebrew example - no time specified:
User: "תזכיר לי לקנות חלב"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Create reminder: buy milk"}},
  "task": {{"title": "לקנות חלב", "due_date": null, "time": null}}
}}

2. **calendar**: A specific event with a time/place.
Hebrew example:
User: "רופא שיניים מחר ב-10"
{{
  "classification": {{"action_type": "calendar", "confidence": 0.95, "summary": "Dentist tomorrow at 10am"}},
  "calendar": {{"summary": "רופא שיניים", "start_time": "2026-02-17 10:00:00", "end_time": "2026-02-17 11:00:00", "location": null, "description": null}}
}}

3. **note**: Information to save for later.
Hebrew example:
User: "הסיסמא לוויפיי היא 12345"
{{
  "classification": {{"action_type": "note", "confidence": 0.9, "summary": "Wifi password"}},
  "note": {{"content": "סיסמת WiFi: 12345", "tags": ["password", "wifi"]}}
}}

4. **query**: A question, request for information, or complex conversation needing context.
Hebrew examples:
User: "מה יש לי ביום רביעי?"
{{
  "classification": {{"action_type": "query", "confidence": 0.85, "summary": "Check Wednesday schedule"}},
  "query": {{"query": "מה יש לי ביום רביעי?", "context_needed": ["calendar"], "target_date": "2026-02-18"}}
}}
User: "יש מיילים חדשים?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Check recent emails"}},
  "query": {{"query": "יש מיילים חדשים?", "context_needed": ["email"], "target_date": null}}
}}
User: "מה קורה עם NVDA?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Check NVDA stock"}},
  "query": {{"query": "מה קורה עם NVDA?", "context_needed": ["market"], "target_date": null}}
}}
User: "מה חדש בעולם ה-AI?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Check AI news"}},
  "query": {{"query": "מה חדש בעולם ה-AI?", "context_needed": ["news"], "target_date": null}}
}}
User: "מה שמרתי על כלי AI?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Search archive about AI tools"}},
  "query": {{"query": "מה שמרתי על כלי AI?", "context_needed": ["archive"], "target_date": null}}
}}
User: "מה שמרתי השבוע?" / "מה שמרתי בחודש האחרון?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Recent archive items"}},
  "query": {{"query": "מה שמרתי השבוע?", "context_needed": ["archive"], "target_date": null, "archive_since": "week"}}
}}
User: "מה זה FastAPI?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Web search: FastAPI"}},
  "query": {{"query": "מה זה FastAPI?", "context_needed": ["web"], "target_date": null}}
}}
User: "יש הזדמנויות בשוק היום?"
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "AI-market synergy"}},
  "query": {{"query": "יש הזדמנויות בשוק היום?", "context_needed": ["synergy"], "target_date": null}}
}}

5. **chat**: Casual greetings, small talk, thanks, opinions, or personal conversation that does NOT need external data.
Hebrew examples:
User: "בוקר טוב" / "מה נשמע" / "תודה" / "אתה הכי טוב"
{{
  "classification": {{"action_type": "chat", "confidence": 0.9, "summary": "Greeting / casual chat"}},
  "query": {{"query": "בוקר טוב", "context_needed": [], "target_date": null}}
}}
User: "מה דעתך על לבנות SaaS לפרילנסרים?"
{{
  "classification": {{"action_type": "chat", "confidence": 0.85, "summary": "Asking for opinion on business idea"}},
  "query": {{"query": "מה דעתך על לבנות SaaS לפרילנסרים?", "context_needed": [], "target_date": null}}
}}

context_needed options: "calendar", "archive", "email", "web", "synergy", "news", "market"
- Use "email" when the user asks about emails, inbox, or messages. Provides deep email intelligence with cited answers when available.
- Use "calendar" for schedule/events questions.
- Use "archive" for saved notes or previously stored knowledge.
- Use "news" when the user asks about AI news, AI developments, "מה חדש ב-AI", tech news, or any AI/tech industry updates.
- Use "market" when the user asks about stocks, stock prices, "מניות", market status, specific tickers, or any financial market data.
- Use "web" for general knowledge questions, real-time events, sports, weather, "מה זה X", or anything needing internet search (NOT for AI news or stocks — use "news" and "market" for those).
- Use "synergy" when the user asks about AI-market opportunities, business ideas from trends, "הזדמנויות", "מה כדאי לבנות".
- Use [] (empty) for casual chat, opinions, ideas, greetings — things that don't need external data.

target_date: When the user asks about a SPECIFIC day (e.g. "יום רביעי", "next Sunday", "February 15th"), compute the exact YYYY-MM-DD date based on Current Date/Time. If the user says a day name without "next" or "last", assume THIS COMING occurrence (the nearest future one). If asking about "today" / "היום", set target_date to null.

Rules:
- If it's a greeting, thanks, opinion, or casual message — classify as "chat" (NOT "query").
- If the user asks a question that needs external data (calendar, tasks, emails, stocks, news, web search) — classify as "query".
- If it's a specific event with time, prefer 'calendar' over 'task'.
- **CRITICAL — TASK CLASSIFICATION**: Only classify as "task" when the user EXPLICITLY asks to CREATE a reminder/task.
  - Create keywords: "תזכיר לי", "צור משימה", "הוסף תזכורת", "תרשום משימה", "remind me", "add task"
  - If the user says "עשיתי", "סיימתי", "מחק", "תמחק", "שנה", "דחה" (complete/delete/edit) — classify as "chat" and respond that tasks are now managed directly in Google Calendar.
  - If the user just MENTIONS something but doesn't explicitly ask — classify as "query" or "chat".
- **ARCHIVE SEARCH**: When the user asks "מה שמרתי על", "what did I save about X" — classify as "query" with context_needed=["archive"].
- **ARCHIVE TEMPORAL**: When the user asks "מה שמרתי השבוע/היום/בחודש האחרון", add "archive_since" field: "today", "week", "month", or "year".
- **CRITICAL**: For all dates and times, convert to ABSOLUTE `YYYY-MM-DD HH:MM:SS` format based on "Current Date/Time". Do NOT return relative strings.
- **CRITICAL**: When the user mentions a day name, calculate the actual date using "Day of week" provided above.
- When in doubt between "task" and "query", prefer "query".
- Return ONLY valid JSON matching the examples above.
- Include ONLY the fields shown in the examples for each action type.
"""

async def _get_recent_context(user_id: int) -> str:
    """Fetch last 5 messages + pending tasks for router context."""
    parts = []
    try:
        # Recent conversation
        resp = (
            supabase.table("interaction_log")
            .select("user_message, bot_response")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        if resp.data:
            lines = []
            for ix in reversed(resp.data):
                lines.append(f"Shay: {ix['user_message'][:150]}")
                lines.append(f"Bot: {ix['bot_response'][:300]}")
            parts.append("=== Recent conversation ===\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Failed to fetch recent conversation for router: {e}")

    return "\n\n".join(parts)


async def route_intent(text: str, user_id: int = None) -> RouterResponse:
    """Classify user text into an action type (task/calendar/note/query/chat) via LLM."""
    try:
        now = datetime.now()
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        current_day = days[now.weekday()]

        # Fetch conversation context if user_id provided
        conversation_context = ""
        if user_id:
            conversation_context = await _get_recent_context(user_id)

        response = await llm_call(
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT.format(
                    current_time=current_time,
                    current_day=current_day,
                    conversation_context=conversation_context,
                )},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=10,
        )

        if not response:
            raise Exception("LLM returned None")

        content = response.choices[0].message.content
        logger.info(f"Router Raw Output: {content}")

        data = json.loads(content)
        return RouterResponse(**data)

    except Exception as e:
        logger.error(f"Router Error: {e}")
        from app.models.router_models import ActionClassification, QueryPayload
        return RouterResponse(
            classification=ActionClassification(
                action_type="query",
                confidence=0.5,
                summary="Fallback due to error"
            ),
            query=QueryPayload(query=text, context_needed=[])
        )
