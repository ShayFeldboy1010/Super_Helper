"""Tests for the Pydantic router models — validates intent classification schemas."""

import json

import pytest
from pydantic import ValidationError

from app.models.router_models import (
    ActionClassification,
    CalendarPayload,
    QueryPayload,
    RouterResponse,
    TaskPayload,
)


class TestActionClassification:
    def test_valid_task_classification(self):
        cls = ActionClassification(
            action_type="task",
            confidence=0.95,
            summary="Create reminder: buy milk",
        )
        assert cls.action_type == "task"
        assert cls.confidence == 0.95

    def test_valid_action_types(self):
        for action in ("task", "calendar", "note", "query", "chat"):
            cls = ActionClassification(
                action_type=action, confidence=0.8, summary="test"
            )
            assert cls.action_type == action

    def test_invalid_action_type_raises(self):
        with pytest.raises(ValidationError):
            ActionClassification(
                action_type="invalid", confidence=0.5, summary="test"
            )


class TestTaskPayload:
    def test_create_action_defaults(self):
        task = TaskPayload(title="Buy milk")
        assert task.action == "create"
        assert task.priority == 0
        assert task.effort is None

    def test_complete_action(self):
        task = TaskPayload(action="complete", title="Shopping")
        assert task.action == "complete"

    def test_edit_with_new_fields(self):
        task = TaskPayload(
            action="edit",
            title="Old title",
            new_title="New title",
            new_due_date="2026-03-01 09:00:00",
        )
        assert task.new_title == "New title"

    def test_recurring_task(self):
        task = TaskPayload(
            title="Exercise",
            recurrence="daily",
            effort="1h",
        )
        assert task.recurrence == "daily"
        assert task.effort == "1h"

    def test_invalid_effort_raises(self):
        with pytest.raises(ValidationError):
            TaskPayload(title="Test", effort="3h")

    def test_invalid_recurrence_raises(self):
        with pytest.raises(ValidationError):
            TaskPayload(title="Test", recurrence="yearly")


class TestCalendarPayload:
    def test_basic_event(self):
        event = CalendarPayload(
            summary="Dentist",
            start_time="2026-03-01 10:00:00",
        )
        assert event.summary == "Dentist"
        assert event.location is None

    def test_event_with_location(self):
        event = CalendarPayload(
            summary="Team meeting",
            start_time="2026-03-01 14:00:00",
            end_time="2026-03-01 15:00:00",
            location="Room 3B",
        )
        assert event.location == "Room 3B"


class TestQueryPayload:
    def test_basic_query(self):
        q = QueryPayload(query="What's on my calendar?", context_needed=["calendar"])
        assert "calendar" in q.context_needed

    def test_multi_context(self):
        q = QueryPayload(
            query="Show me everything",
            context_needed=["calendar", "tasks", "email"],
        )
        assert len(q.context_needed) == 3

    def test_archive_since(self):
        q = QueryPayload(
            query="What did I save this week?",
            context_needed=["archive"],
            archive_since="week",
        )
        assert q.archive_since == "week"

    def test_invalid_context_raises(self):
        with pytest.raises(ValidationError):
            QueryPayload(query="test", context_needed=["invalid_source"])


class TestRouterResponse:
    def test_full_task_response(self):
        data = {
            "classification": {
                "action_type": "task",
                "confidence": 0.9,
                "summary": "Create task",
            },
            "task": {"title": "Buy milk", "due_date": "2026-03-01"},
        }
        resp = RouterResponse(**data)
        assert resp.classification.action_type == "task"
        assert resp.task.title == "Buy milk"
        assert resp.calendar is None

    def test_from_json_string(self):
        """Simulate parsing LLM JSON output."""
        json_str = json.dumps({
            "classification": {
                "action_type": "query",
                "confidence": 0.85,
                "summary": "Check schedule",
            },
            "query": {
                "query": "What do I have tomorrow?",
                "context_needed": ["calendar"],
                "target_date": "2026-03-01",
            },
        })
        data = json.loads(json_str)
        resp = RouterResponse(**data)
        assert resp.query.target_date == "2026-03-01"
