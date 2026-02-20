"""Tests for market data utilities — ticker extraction and name mapping."""

from app.services.market_service import extract_tickers_from_query, COMPANY_TO_TICKER


class TestExtractTickers:
    def test_dollar_sign_ticker(self):
        assert "NVDA" in extract_tickers_from_query("What about $NVDA?")

    def test_company_name_english(self):
        assert "TSLA" in extract_tickers_from_query("How is Tesla doing?")

    def test_company_name_hebrew(self):
        assert "AMZN" in extract_tickers_from_query("מה קורה עם אמזון?")

    def test_multiple_tickers(self):
        result = extract_tickers_from_query("Compare $AAPL and $GOOGL")
        assert "AAPL" in result
        assert "GOOGL" in result

    def test_no_tickers(self):
        assert extract_tickers_from_query("What's for dinner?") == []

    def test_case_insensitive(self):
        assert "NVDA" in extract_tickers_from_query("nvidia stock price")

    def test_hebrew_to_ticker_mapping(self):
        assert COMPANY_TO_TICKER["אנבידיה"] == "NVDA"
        assert COMPANY_TO_TICKER["גוגל"] == "GOOGL"
        assert COMPANY_TO_TICKER["מטא"] == "META"
