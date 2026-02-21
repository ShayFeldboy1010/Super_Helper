"""Tests for iGPT email intelligence service + fallback behavior."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services import igpt_service as igpt


def _make_response(json_data: dict) -> MagicMock:
    """Create a mock httpx.Response (sync .json(), sync .raise_for_status())."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# ask()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ask_returns_output_field():
    """iGPT returns the answer in the 'output' field."""
    mock_resp = _make_response({"output": "You have 3 unread emails from John."})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
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
    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("timeout")):
            result = await igpt.ask("Do I have new emails?")

    assert result is None


@pytest.mark.asyncio
async def test_ask_api_error_returns_none():
    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", side_effect=httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "http://test"), response=httpx.Response(500)
        )):
            result = await igpt.ask("Do I have new emails?")

    assert result is None


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_results():
    mock_resp = _make_response({
        "results": [
            {"subject": "Meeting tomorrow", "from": "boss@co.com", "snippet": "Let's sync"},
            {"subject": "Invoice", "from": "vendor@co.com", "snippet": "Attached"},
        ]
    })

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
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
    mock_resp = _make_response({"results": []})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", return_value=mock_resp) as mock_post:
            await igpt.search("invoices", date_from="2026-01-01", date_to="2026-02-01")
            call_body = mock_post.call_args[1]["json"]

    assert call_body["date_from"] == "2026-01-01"
    assert call_body["date_to"] == "2026-02-01"


@pytest.mark.asyncio
async def test_search_timeout_returns_empty():
    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("timeout")):
            result = await igpt.search("emails")

    assert result == []


# ---------------------------------------------------------------------------
# ask() response field fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ask_unknown_fields_returns_none():
    """If API returns unrecognized fields only, return None instead of raw JSON."""
    mock_resp = _make_response({"id": "abc", "context": {}, "usage": {}})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await igpt.ask("test")

    assert result is None


@pytest.mark.asyncio
async def test_ask_falls_back_to_response_field():
    """If API returns 'response' instead of 'answer', still works."""
    mock_resp = _make_response({"response": "Here is your email summary."})

    with patch.object(igpt, "settings") as mock_settings:
        mock_settings.igpt_enabled = True
        mock_settings.IGPT_API_KEY = "test-key"
        mock_settings.IGPT_API_USER = "user@test.com"

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await igpt.ask("summarize inbox")

    assert result == "Here is your email summary."
