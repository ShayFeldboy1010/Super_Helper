from app.core.llm import llm_call
from app.core.database import supabase
from app.models.router_models import RouterResponse
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """You are a fast intent classifier. Military precision, zero waste.
Classify the user's Hebrew input into one of 5 categories and extract details.

Current Date/Time: {current_time}
Day of week: {current_day}

{conversation_context}

Categories & Schemas:

1. **task**: Create, complete, edit, or delete a task/reminder.
Hebrew example - create:
User: "תזכיר לי לקנות חלב מחר"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Create reminder: buy milk"}},
  "task": {{"action": "create", "title": "לקנות חלב", "due_date": "2026-02-17 09:00:00", "time": null, "priority": 1, "category": "shopping"}}
}}
Hebrew example - complete:
User: "עשיתי את הקניות"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Complete task: shopping"}},
  "task": {{"action": "complete", "title": "הקניות"}}
}}
Hebrew example - complete ALL:
User: "סיימתי הכל" / "כל המשימות בוצעו"
{{
  "classification": {{"action_type": "task", "confidence": 0.95, "summary": "Complete all tasks"}},
  "task": {{"action": "complete_all", "title": ""}}
}}
Example - create recurring:
User: "תזכיר לי כל יום לעשות ספורט"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Create daily recurring task: exercise"}},
  "task": {{"action": "create", "title": "ספורט", "due_date": "2026-02-17 09:00:00", "priority": 1, "recurrence": "daily"}}
}}
Example - edit (rename):
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Rename task: buy milk to buy oat milk"}},
  "task": {{"action": "edit", "title": "Buy milk", "new_title": "Buy oat milk"}}
}}
Example - edit (reschedule):
User: "תדחה את הקניות ליום ראשון"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Reschedule task: shopping to Sunday"}},
  "task": {{"action": "edit", "title": "הקניות", "new_due_date": "2026-02-22 09:00:00"}}
}}
Example - delete:
User: "תמחק את המשימה של הרופא"
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Delete task: doctor"}},
  "task": {{"action": "delete", "title": "הרופא"}}
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

context_needed options: "calendar", "tasks", "archive", "email", "web", "synergy", "news", "market"
- Use "email" when the user asks about emails, inbox, or messages.
- Use "calendar" for schedule/events questions.
- Use "tasks" for to-do related questions.
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
- **CRITICAL — TASK CLASSIFICATION**: Only classify as "task" when the user EXPLICITLY asks to create, complete, edit, or delete a task/reminder.
  - Create keywords: "תזכיר לי", "צור משימה", "הוסף תזכורת", "תרשום משימה", "remind me", "add task"
  - Complete keywords: "סיימתי", "עשיתי", "השלמתי", "בוצע", "completed", "done", "finished"
  - Complete ALL keywords: "כל המשימות בוצעו", "עשיתי הכל", "סיימתי הכל" — use action "complete_all" with empty title
  - Edit keywords: "שנה", "עדכן", "דחה", "postpone", "reschedule", "rename"
  - Delete keywords: "מחק", "תמחק", "הסר", "delete", "remove"
  - Recurring keywords: "כל יום", "כל שבוע", "כל חודש", "every day", "daily", "weekly", "monthly"
  - If the user just MENTIONS something but doesn't explicitly ask — classify as "query" or "chat".
- **ARCHIVE SEARCH**: When the user asks "מה שמרתי על", "what did I save about X" — classify as "query" with context_needed=["archive"].
- **CRITICAL**: For all dates and times, convert to ABSOLUTE `YYYY-MM-DD HH:MM:SS` format based on "Current Date/Time". Do NOT return relative strings.
- **CRITICAL**: When the user mentions a day name, calculate the actual date using "Day of week" provided above.
- When in doubt between "task" and "query", prefer "query".
- Return ONLY valid JSON matching the examples above.
- Include ONLY the fields shown in the examples for each action type.
"""

async def _get_recent_context(user_id: int) -> str:
    """Fetch last 3 messages + pending tasks for router context."""
    parts = []
    try:
        # Recent conversation
        resp = (
            supabase.table("interaction_log")
            .select("user_message, bot_response")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        if resp.data:
            lines = []
            for ix in reversed(resp.data):
                lines.append(f"Shay: {ix['user_message'][:80]}")
                lines.append(f"Bot: {ix['bot_response'][:100]}")
            parts.append("=== Recent conversation ===\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Failed to fetch recent conversation for router: {e}")

    try:
        # Pending tasks (so router can match "mark the blue task" etc.)
        resp = (
            supabase.table("tasks")
            .select("title, due_at, priority")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("priority", desc=True)
            .limit(10)
            .execute()
        )
        if resp.data:
            task_lines = [f"- {t['title']}" for t in resp.data]
            parts.append("=== Open tasks ===\n" + "\n".join(task_lines))
    except Exception as e:
        logger.warning(f"Failed to fetch tasks for router: {e}")

    return "\n\n".join(parts)


async def route_intent(text: str, user_id: int = None) -> RouterResponse:
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
