from groq import AsyncGroq
from app.core.config import settings
from app.models.schemas import TaskCreate
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

client = AsyncGroq(api_key=settings.GROQ_API_KEY)

SYSTEM_PROMPT = """
You are an intelligent personal assistant. Extract task details from the user's natural language input.
Return the output strictly as a JSON object matching the following schema:
{
  "title": "string",
  "due_date": "YYYY-MM-DD" or "today" or "tomorrow" or null,
  "time": "HH:MM" or null,
  "priority": integer (0=low, 1=medium, 2=high, 3=urgent),
  "category": "string" or null
}

Current Date: {current_date}
"""

async def extract_task_intent(text: str) -> TaskCreate:
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct-0905",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(current_date=current_date)},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        logger.info(f"LLM Raw Output: {content}")
        
        data = json.loads(content)
        return TaskCreate(**data)
        
    except Exception as e:
        logger.error(f"Error extracting intent: {e}")
        # Fallback for simple errors or rate limits
        return TaskCreate(title=text, priority=0)
