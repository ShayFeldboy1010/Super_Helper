"""Tests for the LLM wrapper — fallback chain and response formatting."""

import pytest
from app.core.llm import _strip_markdown, _convert_messages, _CompatResponse, _Choice, _Message


class TestStripMarkdown:
    def test_removes_bold(self):
        assert _strip_markdown("**hello**") == "hello"

    def test_removes_italic(self):
        assert _strip_markdown("*hello*") == "hello"

    def test_removes_headers(self):
        assert _strip_markdown("## Title\nContent") == "Title\nContent"

    def test_removes_code_blocks(self):
        assert _strip_markdown("before```python\ncode```after") == "beforeafter"

    def test_removes_inline_code(self):
        assert _strip_markdown("use `pip install`") == "use pip install"

    def test_removes_links(self):
        assert _strip_markdown("[click here](https://example.com)") == "click here"

    def test_removes_blockquotes(self):
        assert _strip_markdown("> quote\nnormal") == "quote\nnormal"

    def test_plain_text_unchanged(self):
        text = "Just a normal sentence with NVDA $190.50"
        assert _strip_markdown(text) == text


class TestConvertMessages:
    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, user = _convert_messages(messages)
        assert system == "You are helpful."
        assert user == "Hello"

    def test_no_system(self):
        messages = [{"role": "user", "content": "Hello"}]
        system, user = _convert_messages(messages)
        assert system is None
        assert user == "Hello"

    def test_multiple_system_messages(self):
        messages = [
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "Hi"},
        ]
        system, user = _convert_messages(messages)
        assert "Rule 1" in system
        assert "Rule 2" in system

    def test_empty_messages(self):
        system, user = _convert_messages([])
        assert system is None
        assert user == ""


class TestCompatResponse:
    def test_default_structure(self):
        resp = _CompatResponse()
        assert len(resp.choices) == 1
        assert resp.choices[0].message.content == ""

    def test_with_content(self):
        resp = _CompatResponse(
            choices=[_Choice(message=_Message(content="Hello"))]
        )
        assert resp.choices[0].message.content == "Hello"
