"""Tests for the task service — CRUD operations, matching, and recurring tasks."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.task_service import (
    _match_task,
    _parse_due_string,
    _spawn_next_recurring,
)

TZ = ZoneInfo("Asia/Jerusalem")


# ---------------------------------------------------------------------------
# _match_task (pure function — no mocks needed)
# ---------------------------------------------------------------------------

class TestMatchTask:
    def test_exact_substring_match(self):
        tasks = [
            {"title": "לקנות חלב"},
            {"title": "להתקשר לרופא"},
        ]
        assert _match_task(tasks, "חלב")["title"] == "לקנות חלב"

    def test_reverse_substring_match(self):
        tasks = [{"title": "API"}]
        assert _match_task(tasks, "finish the API docs")["title"] == "API"

    def test_word_overlap_match(self):
        tasks = [
            {"title": "לכתוב API documentation"},
            {"title": "לקנות מתנה ליום הולדת"},
        ]
        result = _match_task(tasks, "API docs")
        assert result["title"] == "לכתוב API documentation"

    def test_no_match_returns_none(self):
        tasks = [{"title": "לקנות חלב"}]
        assert _match_task(tasks, "xyz unrelated") is None

    def test_empty_tasks_returns_none(self):
        assert _match_task([], "anything") is None

    def test_case_insensitive(self):
        tasks = [{"title": "Fix NGINX Config"}]
        assert _match_task(tasks, "fix nginx")["title"] == "Fix NGINX Config"


# ---------------------------------------------------------------------------
# _parse_due_string (pure function)
# ---------------------------------------------------------------------------

class TestParseDueString:
    def test_today(self):
        result = _parse_due_string("today")
        assert result is not None
        assert result.date() == datetime.now(TZ).date()
        assert result.hour == 9
        assert result.minute == 0

    def test_tomorrow(self):
        result = _parse_due_string("tomorrow")
        expected = (datetime.now(TZ) + timedelta(days=1)).date()
        assert result.date() == expected

    def test_iso_datetime(self):
        result = _parse_due_string("2026-03-15 14:30:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_iso_date_only(self):
        result = _parse_due_string("2026-03-15")
        assert result is not None
        assert result.hour == 9  # defaults to 09:00

    def test_invalid_format_returns_none(self):
        assert _parse_due_string("next tuesday") is None
        assert _parse_due_string("abc") is None


# ---------------------------------------------------------------------------
# _spawn_next_recurring
# ---------------------------------------------------------------------------

class TestSpawnNextRecurring:
    @patch("app.services.task_service.supabase")
    def test_daily_recurrence(self, mock_supabase):
        query = MagicMock()
        query.execute.return_value = MagicMock(data=[{}])
        mock_supabase.table.return_value = query
        query.insert.return_value = query

        task = {
            "user_id": 123,
            "title": "Exercise",
            "priority": 1,
            "recurrence": "daily",
            "due_at": "2026-02-20T09:00:00+02:00",
        }
        _spawn_next_recurring(task)

        mock_supabase.table.assert_called_with("tasks")
        insert_call = query.insert.call_args[0][0]
        assert insert_call["title"] == "Exercise"
        assert insert_call["recurrence"] == "daily"
        assert insert_call["status"] == "pending"

    def test_no_recurrence_does_nothing(self):
        # Should not raise or call anything
        _spawn_next_recurring({"user_id": 1, "title": "One-off"})

    @patch("app.services.task_service.supabase")
    def test_weekly_recurrence(self, mock_supabase):
        query = MagicMock()
        query.execute.return_value = MagicMock(data=[{}])
        mock_supabase.table.return_value = query
        query.insert.return_value = query

        task = {
            "user_id": 123,
            "title": "Weekly review",
            "priority": 2,
            "recurrence": "weekly",
            "due_at": "2026-02-20T09:00:00+02:00",
        }
        _spawn_next_recurring(task)

        insert_call = query.insert.call_args[0][0]
        assert insert_call["recurrence"] == "weekly"


# ---------------------------------------------------------------------------
# create_task (async, needs mocked DB)
# ---------------------------------------------------------------------------

class TestCreateTask:
    @pytest.mark.asyncio
    @patch("app.services.task_service.supabase")
    async def test_create_basic_task(self, mock_supabase):
        query = MagicMock()
        query.execute.return_value = MagicMock(data=[{
            "id": "uuid-1",
            "user_id": 123,
            "title": "Buy milk",
            "status": "pending",
            "priority": 0,
        }])
        query.insert.return_value = query
        query.upsert.return_value = query
        mock_supabase.table.return_value = query

        from app.services.task_service import create_task
        result = await create_task(123, {"title": "Buy milk"})

        assert result is not None
        assert result["title"] == "Buy milk"

    @pytest.mark.asyncio
    @patch("app.services.task_service.supabase")
    async def test_create_task_with_due_date(self, mock_supabase):
        query = MagicMock()
        query.execute.return_value = MagicMock(data=[{
            "id": "uuid-2",
            "user_id": 123,
            "title": "Dentist",
            "due_at": "2026-03-01T10:00:00+02:00",
            "status": "pending",
            "priority": 1,
        }])
        query.insert.return_value = query
        query.upsert.return_value = query
        mock_supabase.table.return_value = query

        from app.services.task_service import create_task
        result = await create_task(123, {
            "title": "Dentist",
            "due_date": "2026-03-01 10:00:00",
            "priority": 1,
        })

        assert result is not None
        assert result["title"] == "Dentist"
        # Verify the insert payload included due_at
        insert_payload = query.insert.call_args[0][0]
        assert "due_at" in insert_payload
