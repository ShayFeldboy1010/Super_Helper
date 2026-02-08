import asyncio
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

INDEX_NAMES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^TA125.TA": "TA-125",
}

TICKER_NAMES = {
    "NVDA": "NVIDIA",
    "MSFT": "Microsoft",
    "GOOGL": "Google",
    "META": "Meta",
    "AAPL": "Apple",
}


async def _fetch_symbol(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Fetch price data for a single symbol from Yahoo Finance chart API."""
    try:
        resp = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"range": "1d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose", 0)
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

        name = INDEX_NAMES.get(symbol) or TICKER_NAMES.get(symbol) or symbol
        return {
            "symbol": symbol,
            "name": name,
            "price": round(price, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return None


async def fetch_market_data() -> dict:
    """Fetch market data for configured indices and tickers."""
    indices_str = getattr(settings, "STOCK_INDICES", "^GSPC,^IXIC,^TA125.TA")
    tickers_str = getattr(settings, "STOCK_WATCHLIST", "NVDA,MSFT,GOOGL,META,AAPL")

    index_symbols = [s.strip() for s in indices_str.split(",") if s.strip()]
    ticker_symbols = [s.strip() for s in tickers_str.split(",") if s.strip()]

    async with httpx.AsyncClient(timeout=10) as client:
        all_results = await asyncio.gather(
            *[_fetch_symbol(client, s) for s in index_symbols + ticker_symbols],
            return_exceptions=True,
        )

    indices = []
    tickers = []
    for i, result in enumerate(all_results):
        if isinstance(result, dict):
            if i < len(index_symbols):
                indices.append(result)
            else:
                tickers.append(result)

    return {"indices": indices, "tickers": tickers}
