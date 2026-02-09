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

# Map common company names to ticker symbols for query detection
COMPANY_TO_TICKER = {
    "amazon": "AMZN", "amzn": "AMZN",
    "tesla": "TSLA", "tsla": "TSLA",
    "netflix": "NFLX", "nflx": "NFLX",
    "nvidia": "NVDA", "nvda": "NVDA",
    "microsoft": "MSFT", "msft": "MSFT",
    "google": "GOOGL", "googl": "GOOGL", "alphabet": "GOOGL",
    "meta": "META", "facebook": "META",
    "apple": "AAPL", "aapl": "AAPL",
    "amd": "AMD",
    "intel": "INTC", "intc": "INTC",
    "palantir": "PLTR", "pltr": "PLTR",
    "coinbase": "COIN", "coin": "COIN",
    "snowflake": "SNOW", "snow": "SNOW",
    "shopify": "SHOP", "shop": "SHOP",
    "uber": "UBER",
    "airbnb": "ABNB", "abnb": "ABNB",
    "spotify": "SPOT", "spot": "SPOT",
    "disney": "DIS", "dis": "DIS",
    "salesforce": "CRM", "crm": "CRM",
    "oracle": "ORCL", "orcl": "ORCL",
    "ibm": "IBM",
    "snap": "SNAP", "snapchat": "SNAP",
    "twitter": "X", "x.com": "X",
    "openai": "MSFT",  # closest publicly traded proxy
    "broadcom": "AVGO", "avgo": "AVGO",
    "arm": "ARM",
    "crowdstrike": "CRWD", "crwd": "CRWD",
    # Hebrew names
    "אמזון": "AMZN", "טסלה": "TSLA", "אפל": "AAPL",
    "גוגל": "GOOGL", "מיקרוסופט": "MSFT", "מטא": "META",
    "נטפליקס": "NFLX", "אנבידיה": "NVDA", "פייסבוק": "META",
    "אינטל": "INTC", "אובר": "UBER", "דיסני": "DIS",
    "אורקל": "ORCL", "סנאפ": "SNAP", "ספוטיפיי": "SPOT",
}


def extract_tickers_from_query(query: str) -> list[str]:
    """Detect ticker symbols and company names in a user query."""
    query_lower = query.lower()
    found = set()

    # Check for $TICKER patterns
    import re
    for match in re.findall(r'\$([A-Za-z]{1,5})', query):
        found.add(match.upper())

    # Check for known company names / tickers
    for name, ticker in COMPANY_TO_TICKER.items():
        if name in query_lower:
            found.add(ticker)

    return list(found)


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


async def fetch_symbols(symbols: list[str]) -> list[dict]:
    """Fetch price data for specific ticker symbols."""
    if not symbols:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(
            *[_fetch_symbol(client, s) for s in symbols],
            return_exceptions=True,
        )
    return [r for r in results if isinstance(r, dict)]


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
