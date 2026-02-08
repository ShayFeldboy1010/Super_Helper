from groq import AsyncGroq
from app.core.config import settings
from app.models.router_models import RouterResponse
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

client = AsyncGroq(api_key=settings.GROQ_API_KEY)

ROUTER_SYSTEM_PROMPT = """
You are the central "Smart Router" for a personal productivity bot. 
Your goal is to classify the user's natural language input into one of 4 categories and extract relevant details.

Current Date/Time: {current_time}

Categories & Schemas:

1. **task**: Something the user needs to do.
Example output:
{{
  "classification": {{"action_type": "task", "confidence": 0.9, "summary": "Buy milk"}},
  "task": {{"title": "Buy milk", "due_date": "tomorrow", "time": null, "priority": 1, "category": "shopping"}}
}}

2. **calendar**: A specific event with a time/place.
Example output:
{{
  "classification": {{"action_type": "calendar", "confidence": 0.95, "summary": "Dentist tomorrow at 10am"}},
  "calendar": {{"summary": "Dentist appointment", "start_time": "tomorrow at 10:00", "end_time": "tomorrow at 11:00", "location": null, "description": null}}
}}

3. **note**: Information to save for later.
Example output:
{{
  "classification": {{"action_type": "note", "confidence": 0.9, "summary": "Wifi password"}},
  "note": {{"content": "Wifi password is 12345", "tags": ["password", "wifi"]}}
}}

4. **query**: A question about schedule or data.
Example output:
{{
  "classification": {{"action_type": "query", "confidence": 0.85, "summary": "Check today's schedule"}},
  "query": {{"query": "What do I have today?", "context_needed": ["calendar", "tasks", "email"]}}
}}

context_needed options: "calendar", "tasks", "archive", "email"
- Use "email" when the user asks about emails, inbox, or messages.
- Use "calendar" for schedule/events, "tasks" for to-dos, "archive" for saved notes.

Example for email query:
{{
  "classification": {{"action_type": "query", "confidence": 0.9, "summary": "Check recent emails"}},
  "query": {{"query": "Do I have any new emails?", "context_needed": ["email"]}}
}}

Rules:
- If it's a greeting or casual message, classify as "query" with query="general greeting" and context_needed=[].
- If it's a specific event with time, prefer 'calendar' over 'task'.
- **CRITICAL**: For all dates and times (start_time, due_date), convert them to ABSOLUTE `YYYY-MM-DD HH:MM:SS` format based on the "Current Date/Time" provided. Do NOT return "tomorrow" or relative strings.
- Return ONLY valid JSON matching the examples above.
- Include ONLY the fields shown in the examples for each action type.
"""

async def route_intent(text: str) -> RouterResponse:
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct-0905",
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT.format(current_time=current_time)},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        logger.info(f"Router Raw Output: {content}")
        print(f"DEBUG: Router Raw Output: {content}")
        
        data = json.loads(content)
        return RouterResponse(**data)
        
    except Exception as e:
        logger.error(f"Router Error: {e}")
        print(f"DEBUG: Router Error: {e}")
        # Fallback: Treat as a Note or Task? Let's Default to Task for safety
        from app.models.router_models import ActionClassification, TaskPayload
        return RouterResponse(
            classification=ActionClassification(
                action_type="task", 
                confidence=0.5, 
                summary="Fallback due to error"
            ),
            task=TaskPayload(title=text)
        )
