from pydantic import BaseModel, Field
from typing import Optional, Literal

class ActionClassification(BaseModel):
    action_type: Literal["task", "calendar", "note", "query"] = Field(..., description="The type of action to perform.")
    confidence: float = Field(..., description="Confidence score between 0 and 1.")
    summary: str = Field(..., description="A brief summary of the user's request.")

class TaskPayload(BaseModel):
    title: str
    due_date: Optional[str] = None
    time: Optional[str] = None
    priority: int = 0  # 0=Low, 1=Medium, 2=High, 3=Urgent
    category: Optional[str] = None

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
    context_needed: list[Literal["calendar", "tasks", "archive", "email"]] = []
    target_date: Optional[str] = None  # YYYY-MM-DD for date-specific queries

class RouterResponse(BaseModel):
    classification: ActionClassification
    task: Optional[TaskPayload] = None
    calendar: Optional[CalendarPayload] = None
    note: Optional[NotePayload] = None
    query: Optional[QueryPayload] = None
