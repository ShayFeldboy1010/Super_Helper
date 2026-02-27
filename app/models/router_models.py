"""Pydantic models for the LLM intent classification router."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ActionClassification(BaseModel):
    action_type: Literal["task", "calendar", "note", "query", "chat"] = Field(..., description="The type of action to perform.")
    confidence: float = Field(..., description="Confidence score between 0 and 1.")
    summary: str = Field(..., description="A brief summary of the user's request.")

class TaskPayload(BaseModel):
    title: str = ""
    due_date: Optional[str] = None
    time: Optional[str] = None

class CalendarPayload(BaseModel):
    summary: str
    start_time: str = Field(..., description="ISO 8601 format or relative time like 'tomorrow at 5pm'")
    end_time: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None

class NotePayload(BaseModel):
    content: str
    tags: list[str] = []

class QueryPayload(BaseModel):
    query: str
    context_needed: list[Literal["calendar", "archive", "email", "web", "synergy", "news", "market"]] = []
    target_date: Optional[str] = None  # YYYY-MM-DD for date-specific queries
    archive_since: Optional[Literal["today", "week", "month", "year"]] = None

class RouterResponse(BaseModel):
    classification: ActionClassification
    task: Optional[TaskPayload] = None
    calendar: Optional[CalendarPayload] = None
    note: Optional[NotePayload] = None
    query: Optional[QueryPayload] = None
