"""User preferences management and pattern inference.

This service handles:
- CRUD for user preferences
- Pattern inference from interaction history
- Enhanced context generation for LLM prompts
- Satisfaction detection
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.database import supabase
from app.models.preference_models import (
    PreferenceUpdate,
    TopicFrequency,
    UserPatterns,
    UserPreferences,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Jerusalem")

# Keywords for satisfaction detection
POSITIVE_WORDS = {
    "תודה", "מעולה", "אחלה", "סבבה", "יופי", "נהדר", "מושלם", "תותח",
    "thanks", "thank you", "perfect", "great", "awesome", "nice", "cool",
    "👍", "❤️", "🙏", "💪", "🔥",
}

NEGATIVE_WORDS = {
    "לא הבנתי", "מה?", "שוב", "לא זה", "טעות", "לא נכון", "בלבול",
    "wrong", "no", "incorrect", "confused", "what?", "huh",
    "👎", "😕", "🤔",
}

# Patterns that suggest the response was unclear
FOLLOWUP_PATTERNS = [
    "מה הכוונה",
    "תסביר",
    "לא הבנתי",
    "עוד פעם",
    "can you explain",
    "what do you mean",
    "i don't understand",
]


async def get_preferences(user_id: int) -> UserPreferences:
    """Get user preferences, creating defaults if not exist."""
    try:
        resp = (
            supabase.table("user_preferences")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if resp.data:
            return UserPreferences(**resp.data[0])

        # Create defaults for new user
        defaults = UserPreferences(user_id=user_id)
        supabase.table("user_preferences").insert(
            defaults.model_dump(exclude_none=True)
        ).execute()
        return defaults

    except Exception as e:
        logger.error(f"Failed to get preferences: {e}")
        return UserPreferences(user_id=user_id)


async def update_preferences(user_id: int, updates: PreferenceUpdate) -> UserPreferences:
    """Update user preferences."""
    try:
        update_data = updates.model_dump(exclude_none=True)
        if not update_data:
            return await get_preferences(user_id)

        # Upsert to handle new users
        supabase.table("user_preferences").upsert(
            {"user_id": user_id, **update_data},
            on_conflict="user_id",
        ).execute()

        return await get_preferences(user_id)

    except Exception as e:
        logger.error(f"Failed to update preferences: {e}")
        return await get_preferences(user_id)


async def get_user_patterns(user_id: int) -> UserPatterns:
    """Get computed patterns from interaction history."""
    try:
        resp = (
            supabase.table("user_patterns")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if resp.data:
            return UserPatterns(**resp.data[0])

        return UserPatterns(user_id=user_id)

    except Exception as e:
        logger.error(f"Failed to get patterns: {e}")
        return UserPatterns(user_id=user_id)


async def get_topic_frequency(user_id: int) -> TopicFrequency:
    """Get topic frequency for interest inference."""
    try:
        resp = (
            supabase.table("user_topic_frequency")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if resp.data:
            return TopicFrequency(**resp.data[0])

        return TopicFrequency(user_id=user_id)

    except Exception as e:
        logger.error(f"Failed to get topic frequency: {e}")
        return TopicFrequency(user_id=user_id)


async def infer_and_update_preferences(user_id: int) -> dict:
    """
    Analyze patterns and update learned preferences.
    Called weekly by cron job.
    Returns summary of changes.
    """
    changes = {"updated": [], "unchanged": []}

    try:
        patterns = await get_user_patterns(user_id)
        topics = await get_topic_frequency(user_id)
        current_prefs = await get_preferences(user_id)

        updates = {}

        # Infer peak_hour
        if patterns.peak_hour is not None and patterns.total_interactions >= 10:
            if current_prefs.peak_hour != patterns.peak_hour:
                updates["peak_hour"] = patterns.peak_hour
                changes["updated"].append(f"peak_hour: {patterns.peak_hour}")

        # Infer morning_person
        if patterns.avg_hour is not None and patterns.total_interactions >= 10:
            is_morning = patterns.avg_hour < 10
            if current_prefs.morning_person != is_morning:
                updates["morning_person"] = is_morning
                changes["updated"].append(f"morning_person: {is_morning}")

        # Infer preferred_day
        if patterns.preferred_day is not None and patterns.total_interactions >= 20:
            if current_prefs.preferred_day != patterns.preferred_day:
                updates["preferred_day"] = patterns.preferred_day
                changes["updated"].append(f"preferred_day: {patterns.preferred_day}")

        # Infer interests from topic frequency
        new_interests = topics.get_interests(threshold=0.15)
        if new_interests and set(new_interests) != set(current_prefs.interests):
            updates["interests"] = new_interests
            changes["updated"].append(f"interests: {new_interests}")

        # Apply updates
        if updates:
            supabase.table("user_preferences").upsert(
                {"user_id": user_id, **updates},
                on_conflict="user_id",
            ).execute()
            logger.info(f"Updated preferences for user {user_id}: {updates}")
        else:
            changes["unchanged"].append("No changes detected")

    except Exception as e:
        logger.error(f"Preference inference failed: {e}")
        changes["error"] = str(e)

    return changes


def detect_satisfaction(message: str) -> str | None:
    """
    Detect user satisfaction from message content.
    Returns 'positive', 'negative', or None.
    """
    message_lower = message.lower().strip()

    # Check for positive signals
    for positive in POSITIVE_WORDS:
        if positive.lower() in message_lower:
            return "positive"

    # Check for negative signals
    for negative in NEGATIVE_WORDS:
        if negative.lower() in message_lower:
            return "negative"

    return None


def is_followup_question(prev_response: str, new_message: str) -> bool:
    """
    Detect if new message is asking for clarification.
    Indicates the previous response was unclear.
    """
    new_lower = new_message.lower()

    for pattern in FOLLOWUP_PATTERNS:
        if pattern in new_lower:
            return True

    # Very short message after long response might be confusion
    if len(new_message) < 10 and len(prev_response) > 200:
        if "?" in new_message:
            return True

    return False


async def update_interaction_satisfaction(
    interaction_id: int,
    satisfaction: str | None,
    had_followup: bool = False,
) -> None:
    """Update satisfaction and followup fields on an interaction."""
    try:
        update_data = {}
        if satisfaction:
            update_data["user_satisfaction"] = satisfaction
        if had_followup:
            update_data["had_followup"] = True

        if update_data:
            supabase.table("interaction_log").update(update_data).eq(
                "id", interaction_id
            ).execute()
    except Exception as e:
        logger.warning(f"Failed to update interaction satisfaction: {e}")


async def get_enhanced_context(user_id: int, action_type: str) -> str:
    """
    Build enhanced context string for LLM prompt injection.
    Combines preferences + patterns + insights.
    """
    parts = []

    try:
        prefs = await get_preferences(user_id)
        patterns = await get_user_patterns(user_id)

        # Section 1: User preferences
        pref_lines = []
        pref_lines.append(f"- Language: {'Hebrew' if prefs.language == 'he' else 'English'}")
        pref_lines.append(f"- Response style: {prefs.response_style}")
        if prefs.interests:
            pref_lines.append(f"- Interests: {', '.join(prefs.interests)}")
        if prefs.morning_person is not None:
            pref_lines.append(f"- {'Morning person' if prefs.morning_person else 'Night owl'}")

        if pref_lines:
            parts.append("=== Shay's Preferences ===")
            parts.extend(pref_lines)

        # Section 2: Behavioral patterns (if enough data)
        if patterns.total_interactions >= 10:
            pattern_lines = []

            if patterns.peak_hour is not None:
                pattern_lines.append(f"- Most active: {patterns.peak_hour}:00")

            # Action preference
            action_counts = {
                "queries": patterns.query_count,
                "tasks": patterns.task_count,
                "calendar": patterns.calendar_count,
                "chat": patterns.chat_count,
            }
            top_action = max(action_counts, key=action_counts.get)
            if action_counts[top_action] > 5:
                pattern_lines.append(f"- Uses mostly: {top_action}")

            # Satisfaction warning
            if patterns.satisfaction_rate < 0.6:
                pattern_lines.append("- Recent responses may have been unclear")

            if pattern_lines:
                parts.append("\n=== Behavioral Patterns ===")
                parts.extend(pattern_lines)

    except Exception as e:
        logger.warning(f"Failed to build enhanced context: {e}")

    return "\n".join(parts)


async def is_quiet_hours(user_id: int) -> bool:
    """Check if current time is within user's quiet hours."""
    try:
        prefs = await get_preferences(user_id)
        current_hour = datetime.now(TZ).hour

        start = prefs.quiet_hours_start
        end = prefs.quiet_hours_end

        # Handle overnight quiet hours (e.g., 22:00 - 07:00)
        if start > end:
            return current_hour >= start or current_hour < end
        else:
            return start <= current_hour < end

    except Exception as e:
        logger.warning(f"Failed to check quiet hours: {e}")
        return False


async def should_send_stock_alerts(user_id: int) -> bool:
    """Check if user wants stock alerts."""
    try:
        prefs = await get_preferences(user_id)
        return prefs.stock_alerts_enabled
    except Exception:
        return True  # Default to sending


async def should_send_daily_brief(user_id: int) -> bool:
    """Check if user wants daily brief."""
    try:
        prefs = await get_preferences(user_id)
        return prefs.daily_brief_enabled
    except Exception:
        return True  # Default to sending
