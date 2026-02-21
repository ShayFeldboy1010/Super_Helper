"""Shared test fixtures — mocks for external services (LLM, Supabase, Google)."""

import os
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set required env vars before any app imports
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("TELEGRAM_USER_ID", "123456")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("M_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SECRET_KEY", "dGVzdC1zZWNyZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=")


@dataclass
class _Message:
    content: str = ""

@dataclass
class _Choice:
    message: _Message = field(default_factory=_Message)

@dataclass
class _LLMResponse:
    """Mimics the ChatCompletion-compatible shape used across the app."""
    choices: list[_Choice] = field(default_factory=lambda: [_Choice()])


def make_llm_response(content: str) -> _LLMResponse:
    """Create a mock LLM response with the given content."""
    return _LLMResponse(choices=[_Choice(message=_Message(content=content))])


@pytest.fixture
def mock_supabase():
    """Mock the Supabase client with chainable query builder."""
    mock = MagicMock()

    # Make table().select().eq()... chains return empty data by default
    query = MagicMock()
    query.execute.return_value = MagicMock(data=[])
    query.eq.return_value = query
    query.neq.return_value = query
    query.gt.return_value = query
    query.lt.return_value = query
    query.gte.return_value = query
    query.lte.return_value = query
    query.in_.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.select.return_value = query
    query.insert.return_value = query
    query.update.return_value = query
    query.upsert.return_value = query
    query.delete.return_value = query
    query.text_search.return_value = query
    query.ilike.return_value = query
    query.overlaps.return_value = query

    mock.table.return_value = query
    return mock


@pytest.fixture
def mock_llm():
    """Mock the LLM call to return predictable responses."""
    with patch("app.core.llm.llm_call", new_callable=AsyncMock) as mock:
        yield mock
