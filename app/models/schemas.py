"""Pydantic schemas for task creation and database representation."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(..., description="The main content or title of the task")
    due_date: Optional[str] = Field(None, description="Due date in YYYY-MM-DD format or 'today', 'tomorrow'")
    time: Optional[str] = Field(None, description="Time in HH:MM format if specified")
    priority: int = Field(0, description="Priority level 0-3, where 3 is highest")
    category: Optional[str] = Field(None, description="Category of the task (e.g., Work, Personal, Shopping)")

class TaskDB(BaseModel):
    id: str
    user_id: int
    title: str
    due_at: Optional[datetime]
    status: Literal['pending', 'completed', 'cancelled']
    priority: int
    created_at: datetime

    model_config = {"from_attributes": True}
