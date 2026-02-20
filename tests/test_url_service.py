"""Tests for URL extraction and content processing."""

from app.services.url_service import extract_urls


class TestExtractUrls:
    def test_single_url(self):
        text = "Check this out https://example.com/article"
        urls = extract_urls(text)
        assert urls == ["https://example.com/article"]

    def test_multiple_urls(self):
        text = "See https://a.com and http://b.com/path"
        urls = extract_urls(text)
        assert len(urls) == 2

    def test_no_urls(self):
        text = "Just a regular message without links"
        assert extract_urls(text) == []

    def test_url_with_query_params(self):
        text = "Visit https://example.com/search?q=test&lang=en"
        urls = extract_urls(text)
        assert len(urls) == 1
        assert "q=test" in urls[0]

    def test_hebrew_text_with_url(self):
        text = "תראה את זה https://news.ycombinator.com/item?id=12345 מעניין"
        urls = extract_urls(text)
        assert len(urls) == 1
        assert "ycombinator" in urls[0]
