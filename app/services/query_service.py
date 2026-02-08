import logging
import json
from app.core.database import supabase
from app.services.google_svc import GoogleService
from groq import AsyncGroq
from app.core.config import settings
import os

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)

class QueryService:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.google = GoogleService(user_id)

    async def answer_query(self, query_text: str, context_needed: list[str], target_date: str = None, memory_context: str = "") -> str:
        context_data = []

        # 1. Fetch Calendar if needed or default
        if "calendar" in context_needed or not context_needed:
            events = await self.google.get_events_for_date(target_date)
            date_label = target_date if target_date else "היום"
            context_data.append(f"📅 אירועים ב-{date_label}:\n" + "\n".join(events))

        # 2. Fetch Tasks if needed or default
        if "tasks" in context_needed or not context_needed:
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
        if "notes" in context_needed:
             try:
                response = supabase.table("archive").select("content, tags").eq("user_id", self.user_id).order("created_at", desc=True).limit(5).execute()
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

        # 5. Generate Answer with LLM
        full_context = "\n\n".join(context_data)

        system_prompt = (
            "אתה ראש מטה אישי (Chief of Staff). פורמט BLUF — שורה תחתונה קודם.\n"
            "ענה תמיד בעברית. תמציתי, ישיר, ללא מילות מילוי.\n"
            "• בולטים, לא פסקאות\n"
            "• המידע החשוב ביותר — קודם\n"
            "• אם אין תשובה במידע — אמור שאינך יודע\n"
            "• אל תוסיף סיסמאות מוטיבציה. תן מידע, לא נאומים.\n"
            "• אם יש פעולה מומלצת — הצע אותה בסוף"
        )

        if memory_context:
            system_prompt += (
                "\n\nמידע שנצבר על המשתמש (השתמש בו בטבעיות, בלי להזכיר שיש לך אותו):\n"
                + memory_context
            )

        try:
            chat_completion = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{full_context}\n\nQuestion: {query_text}"}
                ],
                model="moonshotai/kimi-k2-instruct-0905",
                temperature=0.7,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return "❌ נתקלתי בשגיאה בעת בדיקת הנתונים שלך."
