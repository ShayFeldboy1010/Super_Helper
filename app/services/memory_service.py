import logging
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.database import supabase
from app.core.llm import llm_call

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Jerusalem")

# Maps action_type to relevant insight categories
CATEGORY_MAP = {
    "task": ["goal", "habit", "work", "preference"],
    "calendar": ["relationship", "work", "health", "preference"],
    "note": ["preference", "work", "goal"],
    "query": None,  # None means all categories
}


async def log_interaction(
    user_id: int,
    user_message: str,
    bot_response: str,
    action_type: str,
    intent_summary: str = None,
    telegram_update_id: int = None,
):
    """Insert into interaction_log. Errors are swallowed — never blocks the user."""
    try:
        payload = {
            "user_id": user_id,
            "user_message": user_message,
            "bot_response": bot_response,
            "action_type": action_type,
            "intent_summary": intent_summary,
        }
        if telegram_update_id:
            payload["telegram_update_id"] = telegram_update_id
        supabase.table("interaction_log").insert(payload).execute()
    except Exception as e:
        logger.error(f"Failed to log interaction: {e}")


async def get_relevant_insights(
    user_id: int,
    action_type: str,
    query_text: str = "",
    max_insights: int = 8,
) -> str:
    """
    Retrieve relevant permanent insights for prompt injection.
    Returns a formatted string (empty string if nothing found).
    """
    try:
        results = []

        # Tier 1: Category filter
        categories = CATEGORY_MAP.get(action_type)
        if categories is not None:
            resp = (
                supabase.table("permanent_insights")
                .select("insight, category, confidence")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .in_("category", categories)
                .order("confidence", desc=True)
                .limit(max_insights)
                .execute()
            )
            results.extend(resp.data or [])

        # Tier 2: FTS keyword search if query_text has substance
        if query_text and len(query_text.strip()) > 2:
            words = [w for w in query_text.strip().split() if len(w) > 1]
            if words:
                ts_query = " | ".join(words)
                try:
                    fts_resp = (
                        supabase.table("permanent_insights")
                        .select("insight, category, confidence")
                        .eq("user_id", user_id)
                        .eq("is_active", True)
                        .limit(max_insights)
                        .text_search("fts", ts_query)
                        .execute()
                    )
                    results.extend(fts_resp.data or [])
                except Exception as fts_err:
                    logger.warning(f"FTS search failed, skipping: {fts_err}")

        if not results:
            return ""

        # Deduplicate by insight text
        seen = set()
        unique = []
        for r in results:
            if r["insight"] not in seen:
                seen.add(r["insight"])
                unique.append(r)

        unique = unique[:max_insights]

        lines = [f"- [{r['category']}] {r['insight']}" for r in unique]
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to get insights: {e}")
        return ""


REFLECTION_PROMPT = """You analyze interactions between a user and a personal assistant.
Your goal: extract permanent facts, preferences, habits, and important information about the user.

Rules:
- Only extract information that is factual and permanent (not one-time)
- Each insight should be a short, clear sentence
- Possible categories: goal, habit, work, preference, health, relationship, finance
- Do not repeat insights that already exist in the "existing insights" list
- If an interaction reinforces an existing insight, note it

Return JSON in this format:
{
  "new_insights": [
    {"category": "...", "insight": "...", "source_summary": "..."}
  ],
  "reinforced_insights": [
    {"insight_text": "...", "reason": "..."}
  ]
}

If there are no new insights, return empty lists."""


FOLLOWUP_EXTRACTION_PROMPT = """You analyze conversations between a user and a personal assistant.
Your goal: extract action items, commitments, and things the user said they'd do.

Look for patterns like:
- "I'll do X", "I need to send X", "Let me think about X"
- "I should call/email/message someone"
- "I'll handle that tomorrow/next week"
- "Remind me to...", "Don't let me forget..."

Do NOT extract:
- Tasks that were already created via the task system (bot confirmed task creation)
- Vague intentions with no concrete action ("I guess I could...")
- Things the bot will do (not the user)

Return JSON:
{
  "follow_ups": [
    {
      "commitment": "Short description of what they committed to",
      "source_quote": "The exact or near-exact quote from the conversation",
      "suggested_due": "YYYY-MM-DD or null if no timeframe mentioned"
    }
  ]
}

If no commitments found, return {"follow_ups": []}."""


async def extract_follow_ups(user_id: int) -> int:
    """Extract follow-ups from today's conversations. Returns count of new follow-ups."""
    try:
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        resp = (
            supabase.table("interaction_log")
            .select("user_message, bot_response, action_type")
            .eq("user_id", user_id)
            .in_("action_type", ["query", "note", "calendar"])
            .gte("created_at", f"{today_str}T00:00:00")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        interactions = resp.data or []
        if not interactions:
            return 0

        conversation_block = "\n---\n".join(
            f"User: {ix['user_message']}\nBot: {ix['bot_response']}"
            for ix in interactions
        )

        chat_completion = await llm_call(
            messages=[
                {"role": "system", "content": FOLLOWUP_EXTRACTION_PROMPT},
                {"role": "user", "content": conversation_block},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=10,
            tier="flash",
        )
        if not chat_completion:
            return 0

        result = json.loads(chat_completion.choices[0].message.content)
        count = 0
        for fu in result.get("follow_ups", []):
            try:
                payload = {
                    "user_id": user_id,
                    "commitment": fu["commitment"],
                    "source_message": fu.get("source_quote", ""),
                }
                if fu.get("suggested_due"):
                    payload["due_at"] = fu["suggested_due"]
                supabase.table("follow_ups").insert(payload).execute()
                count += 1
            except Exception as e:
                logger.error(f"Failed to insert follow-up: {e}")

        return count
    except Exception as e:
        logger.error(f"Follow-up extraction error: {e}")
        return 0


async def get_pending_follow_ups(user_id: int, limit: int = 5) -> list[dict]:
    """Get pending follow-ups ordered by due date."""
    try:
        resp = (
            supabase.table("follow_ups")
            .select("id, commitment, due_at, reminded_count, extracted_at")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .order("due_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.error(f"Failed to get follow-ups: {e}")
        return []


async def run_daily_reflection(user_id: int) -> dict:
    """
    Fetch unprocessed interactions, extract insights via LLM,
    upsert into permanent_insights. Returns summary dict.
    """
    summary = {"interactions_analyzed": 0, "new_insights": 0, "reinforced_insights": 0}

    try:
        # 1. Fetch unprocessed interactions
        resp = (
            supabase.table("interaction_log")
            .select("id, user_message, bot_response, action_type, intent_summary")
            .eq("user_id", user_id)
            .eq("reflection_processed", False)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        interactions = resp.data or []
        if not interactions:
            return summary

        summary["interactions_analyzed"] = len(interactions)

        # 2. Fetch existing insights for dedup
        existing_resp = (
            supabase.table("permanent_insights")
            .select("id, insight, category")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )
        existing_insights = existing_resp.data or []
        existing_text = "\n".join(
            [f"- [{e['category']}] {e['insight']}" for e in existing_insights]
        )

        # 3. Build LLM prompt
        interaction_lines = []
        for ix in interactions:
            interaction_lines.append(
                f"User: {ix['user_message']}\nBot: {ix['bot_response']}\nType: {ix['action_type']}"
            )
        interaction_block = "\n---\n".join(interaction_lines)

        user_prompt = (
            f"Existing insights:\n{existing_text or 'None yet'}\n\n"
            f"New interactions:\n{interaction_block}"
        )

        # 4. Call LLM
        chat_completion = await llm_call(
            messages=[
                {"role": "system", "content": REFLECTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=15,
            tier="pro",
        )
        if not chat_completion:
            logger.error("LLM returned None for daily reflection")
            return summary

        result = json.loads(chat_completion.choices[0].message.content)

        # 5. Insert new insights
        for ins in result.get("new_insights", []):
            try:
                supabase.table("permanent_insights").insert({
                    "user_id": user_id,
                    "category": ins["category"],
                    "insight": ins["insight"],
                    "source_summary": ins.get("source_summary", ""),
                }).execute()
                summary["new_insights"] += 1
            except Exception as e:
                logger.error(f"Failed to insert insight: {e}")

        # 6. Reinforce existing insights
        for reinf in result.get("reinforced_insights", []):
            try:
                # Find matching existing insight by text similarity
                match = None
                for ex in existing_insights:
                    if reinf["insight_text"].strip().lower() in ex["insight"].lower():
                        match = ex
                        break

                if match:
                    # Fetch current values for proper increment
                    current = (
                        supabase.table("permanent_insights")
                        .select("times_reinforced, confidence")
                        .eq("id", match["id"])
                        .execute()
                    ).data[0]
                    supabase.table("permanent_insights").update({
                        "times_reinforced": current["times_reinforced"] + 1,
                        "last_reinforced_at": "now()",
                        "confidence": min(1.0, current["confidence"] + 0.05),
                    }).eq("id", match["id"]).execute()
                    summary["reinforced_insights"] += 1
            except Exception as e:
                logger.error(f"Failed to reinforce insight: {e}")

        # 7. Mark interactions as processed
        interaction_ids = [ix["id"] for ix in interactions]
        for iid in interaction_ids:
            try:
                supabase.table("interaction_log").update({
                    "reflection_processed": True
                }).eq("id", iid).execute()
            except Exception as e:
                logger.error(f"Failed to mark interaction {iid} as processed: {e}")

    except Exception as e:
        logger.error(f"Daily reflection error: {e}")

    return summary
