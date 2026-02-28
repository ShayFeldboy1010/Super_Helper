"""Pydantic models for user preferences and learning system."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserPreferences(BaseModel):
    """User preferences - both explicit and learned."""

    user_id: int

    # Explicit preferences (user-set)
    language: Literal["he", "en"] = "he"
    response_style: Literal["concise", "detailed"] = "concise"
    quiet_hours_start: int = Field(default=22, ge=0, le=23)
    quiet_hours_end: int = Field(default=7, ge=0, le=23)
    stock_alerts_enabled: bool = True
    daily_brief_enabled: bool = True

    # Learned preferences (auto-detected)
    peak_hour: Optional[int] = None
    preferred_day: Optional[int] = None
    interests: list[str] = []
    morning_person: Optional[bool] = None


class UserPatterns(BaseModel):
    """Computed patterns from interaction history."""

    user_id: int
    total_interactions: int = 0

    # Time patterns
    peak_hour: Optional[int] = None
    preferred_day: Optional[int] = None
    avg_hour: Optional[float] = None

    # Action distribution
    query_count: int = 0
    task_count: int = 0
    calendar_count: int = 0
    chat_count: int = 0
    note_count: int = 0

    # Satisfaction
    positive_count: int = 0
    negative_count: int = 0
    followup_count: int = 0
    avg_response_length: Optional[float] = None

    @property
    def satisfaction_rate(self) -> float:
        """Percentage of positive interactions."""
        total = self.positive_count + self.negative_count
        if total == 0:
            return 1.0  # No data = assume good
        return self.positive_count / total

    @property
    def clarity_rate(self) -> float:
        """Percentage of interactions without follow-up questions."""
        if self.total_interactions == 0:
            return 1.0
        return 1 - (self.followup_count / self.total_interactions)


class TopicFrequency(BaseModel):
    """Topic frequency for interest inference."""

    user_id: int
    stock_queries: int = 0
    ai_queries: int = 0
    productivity_queries: int = 0
    total_queries: int = 0

    def get_interests(self, threshold: float = 0.15) -> list[str]:
        """Return interests that exceed threshold percentage."""
        if self.total_queries == 0:
            return []

        interests = []
        if self.stock_queries / self.total_queries > threshold:
            interests.append("stocks")
        if self.ai_queries / self.total_queries > threshold:
            interests.append("AI")
        if self.productivity_queries / self.total_queries > threshold:
            interests.append("productivity")

        return interests


class PreferenceUpdate(BaseModel):
    """Request model for updating preferences."""

    language: Optional[Literal["he", "en"]] = None
    response_style: Optional[Literal["concise", "detailed"]] = None
    quiet_hours_start: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_hours_end: Optional[int] = Field(default=None, ge=0, le=23)
    stock_alerts_enabled: Optional[bool] = None
    daily_brief_enabled: Optional[bool] = None
