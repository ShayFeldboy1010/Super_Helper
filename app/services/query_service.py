import logging
import json
from app.core.database import supabase
from app.services.google_svc import GoogleService
from app.services.search_service import web_search, format_search_results
from groq import AsyncGroq
from app.core.config import settings
from app.core.prompts import CHIEF_OF_STAFF_IDENTITY

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)


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
                lines.append(f"שי: {ix['user_message'][:100]}")
                lines.append(f"אתה: {ix['bot_response'][:150]}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error fetching recent conversation: {e}")
            return ""

    async def answer_query(self, query_text: str, context_needed: list[str], target_date: str = None, memory_context: str = "") -> str:
        context_data = []

        # 1. Fetch Calendar if explicitly needed
        if "calendar" in context_needed:
            events = await self.google.get_events_for_date(target_date)
            date_label = target_date if target_date else "היום"
            context_data.append(f"📅 אירועים ב-{date_label}:\n" + "\n".join(events))

        # 2. Fetch Tasks if explicitly needed
        if "tasks" in context_needed:
            try:
                response = supabase.table("tasks").select("*").eq("user_id", self.user_id).eq("status", "pending").execute()
                tasks = response.data
                if tasks:
                    task_list = "\n".join([f"- {t['title']} (Due: {t.get('due_at')})" for t in tasks])
                    context_data.append(f"✅ משימות פתוחות:\n{task_list}")
                else:
                    context_data.append("✅ אין משימות פתוחות.")
            except Exception as e:
                logger.error(f"Error fetching tasks: {e}")

        # 3. Fetch Notes
        if "notes" in context_needed or "archive" in context_needed:
            try:
                response = supabase.table("archive").select("content, tags, metadata").eq("user_id", self.user_id).order("created_at", desc=True).limit(5).execute()
                notes = response.data
                if notes:
                    note_list = "\n".join([f"- {n['content']} (Tags: {n['tags']})" for n in notes])
                    context_data.append(f"📝 הערות אחרונות:\n{note_list}")
            except Exception as e:
                logger.error(f"Error fetching notes: {e}")

        # 4. Fetch Emails
        if "email" in context_needed:
            try:
                emails = await self.google.get_recent_emails(max_results=5)
                if emails:
                    email_lines = []
                    for e in emails:
                        email_lines.append(f"- מאת: {e['from']} | נושא: {e['subject']}\n  {e['snippet'][:100]}")
                    context_data.append(f"📧 אימיילים אחרונים:\n" + "\n".join(email_lines))
                else:
                    context_data.append("📧 אין אימיילים אחרונים.")
            except Exception as e:
                logger.error(f"Error fetching emails: {e}")

        # 5. Web Search
        if "web" in context_needed:
            try:
                results = await web_search(query_text, max_results=5)
                if results:
                    context_data.append(f"🌐 תוצאות חיפוש:\n{format_search_results(results)}")
            except Exception as e:
                logger.error(f"Error in web search: {e}")

        # 6. Recent conversation for continuity
        recent_convo = await self._get_recent_conversation(limit=5)

        # 7. Build system prompt with all context
        full_context = "\n\n".join(context_data) if context_data else ""

        system_prompt = CHIEF_OF_STAFF_IDENTITY

        if recent_convo:
            system_prompt += (
                "\n\n═══ שיחה אחרונה (להמשכיות) ═══\n"
                + recent_convo
            )

        if memory_context:
            system_prompt += (
                "\n\n═══ מה שאתה יודע על שי ═══\n"
                + memory_context
            )

        # 8. Build user message
        if full_context:
            user_content = f"מידע רלוונטי:\n{full_context}\n\nשי אומר: {query_text}"
        else:
            user_content = query_text

        try:
            chat_completion = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                model="moonshotai/kimi-k2-instruct-0905",
                temperature=0.7,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return "משהו השתבש. נסה שוב."
