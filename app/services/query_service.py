import logging
import json
from app.core.database import supabase
from app.services.google_svc import GoogleService
# from app.services.llm_engine import LLMEngine # Removed unused import
from groq import AsyncGroq
from app.core.config import settings
import os

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)

class QueryService:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.google = GoogleService(user_id)

    async def answer_query(self, query_text: str, context_needed: list[str]) -> str:
        context_data = []

        # 1. Fetch Calendar if needed or default
        if "calendar" in context_needed or not context_needed:
            events = await self.google.get_todays_events()
            context_data.append(f"📅 Current Calendar Events:\n" + "\n".join(events))

        # 2. Fetch Tasks if needed or default
        if "tasks" in context_needed or not context_needed:
            # Fetch pending tasks from DB
            try:
                response = supabase.table("tasks").select("*").eq("user_id", self.user_id).eq("status", "pending").execute()
                tasks = response.data
                if tasks:
                    task_list = "\n".join([f"- {t['title']} (Due: {t.get('due_at')})" for t in tasks])
                    context_data.append(f"✅ Pending Tasks:\n{task_list}")
                else:
                    context_data.append("✅ No pending tasks.")
            except Exception as e:
                logger.error(f"Error fetching tasks: {e}")

        # 3. Fetch Notes (Naive: last 5)
        if "notes" in context_needed:
             try:
                response = supabase.table("archive").select("content, tags").eq("user_id", self.user_id).order("created_at", desc=True).limit(5).execute()
                notes = response.data
                if notes:
                    note_list = "\n".join([f"- {n['content']} (Tags: {n['tags']})" for n in notes])
                    context_data.append(f"📝 Recent Notes:\n{note_list}")
             except Exception as e:
                logger.error(f"Error fetching notes: {e}")

        # 4. Generate Answer with LLM
        full_context = "\n\n".join(context_data)
        
        system_prompt = (
            "You are a helpful personal assistant. Answer the user's question based ONLY on the provided context.\n"
            "If the answer is not in the context, say you don't know or suggest checking elsewhere.\n"
            "Keep the answer concise and friendly."
        )
        
        try:
            chat_completion = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{full_context}\n\nQuestion: {query_text}"}
                ],
                model="llama3-8b-8192", # Fast and good enough for synthesis
                temperature=0.7,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            return "❌ I encountered an error checking your data."
