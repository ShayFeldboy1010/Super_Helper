"""Tests for the LLM wrapper — fallback chain and response formatting."""

from app.core.llm import _Choice, _CompatResponse, _convert_messages, _md_to_telegram_html, _Message


class TestMdToTelegramHtml:
    def test_converts_bold(self):
        assert _md_to_telegram_html("**hello**") == "<b>hello</b>"

    def test_converts_italic(self):
        assert _md_to_telegram_html("*hello*") == "<i>hello</i>"

    def test_converts_headers(self):
        assert _md_to_telegram_html("## Title\nContent") == "<b>Title</b>\nContent"

    def test_strips_code_blocks(self):
        assert _md_to_telegram_html("before```python\ncode```after") == "beforeafter"

    def test_converts_inline_code(self):
        assert _md_to_telegram_html("use `pip install`") == "use <code>pip install</code>"

    def test_converts_links(self):
        assert _md_to_telegram_html("[click here](https://example.com)") == '<a href="https://example.com">click here</a>'

    def test_strips_blockquotes(self):
        assert _md_to_telegram_html("> quote\nnormal") == "quote\nnormal"

    def test_plain_text_unchanged(self):
        text = "Just a normal sentence with NVDA $190.50"
        assert _md_to_telegram_html(text) == text

    def test_html_escapes_dangerous_input(self):
        result = _md_to_telegram_html("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


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
