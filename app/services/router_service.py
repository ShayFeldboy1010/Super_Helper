from groq import AsyncGroq
from app.core.config import settings
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY
from app.models.router_models import RouterResponse
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

client = AsyncGroq(api_key=settings.GROQ_API_KEY)

ROUTER_SYSTEM_PROMPT = CHIEF_OF_STAFF_IDENTITY + """

═══ הנחיות ניתוב ═══
Classify the user's natural language input into one of 4 categories and extract relevant details.
The user speaks Hebrew. Understand Hebrew input.

Current Date/Time: {current_time}
Day of week: {current_day}

Categories & Schemas:

1. **task**: Something the user needs to do.
Example output:
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Buy milk"}},
  "task": {{"title": "Buy milk", "due_date": "2026-02-09 09:00:00", "time": null, "priority": 1, "category": "shopping"}}
}}

2. **calendar**: A specific event with a time/place.
Example output:
{{
  "classification": {{"action_type": "calendar", "confidence": 0.95, "summary": "Dentist tomorrow at 10am"}},
  "calendar": {{"summary": "Dentist appointment", "start_time": "2026-02-09 10:00:00", "end_time": "2026-02-09 11:00:00", "location": null, "description": null}}
}}

3. **note**: Information to save for later.
Example output:
{{
  "classification": {{"action_type": "note", "confidence": 0.9, "summary": "Wifi password"}},
  "note": {{"content": "Wifi password is 12345", "tags": ["password", "wifi"]}}
}}

4. **query**: A question, conversation, request for information, or general chat.
Example - asking about a specific day:
{{
  "classification": {{"action_type": "query", "confidence": 0.85, "summary": "Check Wednesday schedule"}},
  "query": {{"query": "What do I have on Wednesday?", "context_needed": ["calendar"], "target_date": "2026-02-11"}}
}}

Example - asking about today:
{{
  "classification": {{"action_type": "query", "confidence": 0.85, "summary": "Check today's schedule"}},
  "query": {{"query": "What do I have today?", "context_needed": ["calendar", "tasks"], "target_date": null}}
}}

Example - asking about emails:
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Check recent emails"}},
  "query": {{"query": "Do I have any new emails?", "context_needed": ["email"], "target_date": null}}
}}

Example - web search / general knowledge question:
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Search for latest AI news"}},
  "query": {{"query": "What are the latest developments in autonomous driving?", "context_needed": ["web"], "target_date": null}}
}}

Example - casual conversation or opinion:
{{
  "classification": {{"action_type": "query", "confidence": 0.8, "summary": "Casual chat about project idea"}},
  "query": {{"query": "What do you think about building a SaaS for freelancers?", "context_needed": [], "target_date": null}}
}}

Example - asking for advice/ideas:
{{
  "classification": {{"action_type": "query", "confidence": 0.85, "summary": "Asking for business ideas"}},
  "query": {{"query": "Give me ideas for a side project", "context_needed": [], "target_date": null}}
}}

context_needed options: "calendar", "tasks", "archive", "email", "web"
- Use "email" when the user asks about emails, inbox, or messages.
- Use "calendar" for schedule/events questions.
- Use "tasks" for to-do related questions.
- Use "archive" for saved notes or previously stored knowledge.
- Use "web" when the user asks about current events, factual questions, searches, "what is X", "find me Y", or anything that needs up-to-date information from the internet.
- Use [] (empty) for casual chat, opinions, ideas, advice, greetings — things that don't need external data.

target_date: When the user asks about a SPECIFIC day (e.g. "Wednesday", "next Sunday", "February 15th"), compute the exact YYYY-MM-DD date based on Current Date/Time and set it as target_date. If the user says a day name without "next" or "last", assume THIS COMING occurrence (the nearest future one). If asking about "today", set target_date to null.

Rules:
- If it's a greeting or casual message, classify as "query" with the ACTUAL text as query (not "general greeting") and context_needed=[].
- If it's a specific event with time, prefer 'calendar' over 'task'.
- If the user asks a general knowledge question, opinion, or wants to chat — classify as "query". The bot can answer anything.
- When the user asks about a company, person, product, or topic — classify as "query" with context_needed=["web"] so the bot searches for real info.
- **CRITICAL — TASK CLASSIFICATION**: Only classify as "task" when the user EXPLICITLY asks to create a task, reminder, or to-do. Keywords: "תזכיר לי", "צור משימה", "הוסף תזכורת", "תרשום משימה", "remind me", "add task". If the user just MENTIONS something they need to do but doesn't ask to create a reminder — classify as "query" and let the conversation flow.
- **CRITICAL**: For all dates and times (start_time, due_date), convert them to ABSOLUTE `YYYY-MM-DD HH:MM:SS` format based on the "Current Date/Time" provided. Do NOT return "tomorrow" or relative strings.
- **CRITICAL**: When the user mentions a day name like "Wednesday" / "יום רביעי", calculate the actual date of THIS week's occurrence (or next week if that day has already passed). Use the "Day of week" provided above to calculate.
- When in doubt between "task" and "query", prefer "query". The user will explicitly ask if they want a reminder.
- Return ONLY valid JSON matching the examples above.
- Include ONLY the fields shown in the examples for each action type.
"""

async def route_intent(text: str) -> RouterResponse:
    try:
        now = datetime.now()
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        current_day = days[now.weekday()]

        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct-0905",
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT.format(
                    current_time=current_time,
                    current_day=current_day
                )},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )

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
