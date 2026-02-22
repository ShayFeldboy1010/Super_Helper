"""Tests for iGPT email intelligence service + fallback behavior."""

from unittest.mock import MagicMock, patch

import pytest

from app.services import igpt_service as igpt


def _mock_client(ask_return=None, search_return=None):
    """Create a mock iGPT SDK client."""
    client = MagicMock()
    client.recall.ask.return_value = ask_return
    client.recall.search.return_value = search_return
    return client


# ---------------------------------------------------------------------------
# ask()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ask_returns_output_field():
    """iGPT returns the answer in the 'output' field."""
    client = _mock_client(ask_return={"output": "You have 3 unread emails from John."})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.ask("Do I have new emails?")

    assert result == "You have 3 unread emails from John."


@pytest.mark.asyncio
async def test_ask_disabled_returns_none():
    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = False
        result = await igpt.ask("Do I have new emails?")

    assert result is None


@pytest.mark.asyncio
async def test_ask_timeout_returns_none():
    client = _mock_client()
    client.recall.ask.side_effect = TimeoutError("timeout")

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.ask("Do I have new emails?")

    assert result is None


@pytest.mark.asyncio
async def test_ask_api_error_returns_none():
    client = _mock_client(ask_return={"error": "auth"})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.ask("Do I have new emails?")

    assert result is None


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_results():
    results = [
        {"subject": "Meeting tomorrow", "from": "boss@co.com", "snippet": "Let's sync"},
        {"subject": "Invoice", "from": "vendor@co.com", "snippet": "Attached"},
    ]
    client = _mock_client(search_return={"results": results})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.search("meeting emails")

    assert len(result) == 2
    assert result[0]["subject"] == "Meeting tomorrow"


@pytest.mark.asyncio
async def test_search_disabled_returns_empty():
    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = False
        result = await igpt.search("meeting emails")

    assert result == []


@pytest.mark.asyncio
async def test_search_with_date_filters():
    client = _mock_client(search_return={"results": []})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            await igpt.search("invoices", date_from="2026-01-01", date_to="2026-02-01")
            call_kwargs = client.recall.search.call_args[1]

    assert call_kwargs["date_from"] == "2026-01-01"
    assert call_kwargs["date_to"] == "2026-02-01"


@pytest.mark.asyncio
async def test_search_timeout_returns_empty():
    client = _mock_client()
    client.recall.search.side_effect = TimeoutError("timeout")

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.search("emails")

    assert result == []


# ---------------------------------------------------------------------------
# ask() response handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ask_unknown_fields_returns_none():
    """If SDK returns unrecognized fields only, return None."""
    client = _mock_client(ask_return={"id": "abc", "context": {}, "usage": {}})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.ask("test")

    assert result is None


@pytest.mark.asyncio
async def test_ask_none_response_returns_none():
    """If SDK returns None, return None."""
    client = _mock_client(ask_return=None)

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch.object(igpt, "_get_client", return_value=client):
            result = await igpt.ask("summarize inbox")

    assert result is None
