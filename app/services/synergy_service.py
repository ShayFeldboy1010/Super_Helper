import logging

from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)
client = AsyncGroq(api_key=settings.GROQ_API_KEY)

SYNERGY_PROMPT = """You are a sharp business analyst connecting AI developments with market movements.

You receive:
1. Today's AI news headlines
2. Today's market data (indices + tech stocks)
3. What you know about the user's projects and interests

Your job: Find 2-3 concrete connections between AI developments and market signals, personalized to the user.

Format each insight as:
[emoji] AI development -> Market signal -> Business opportunity for the user

Rules:
- Plain text only, NO markdown (no **, no ##, no bullets with *)
- Use arrow -> to show the chain of logic
- Each insight gets one emoji header
- Be specific and actionable, not generic
- Connect dots the user wouldn't see on their own
- If user has projects/interests, tie insights back to them
- Keep each insight to 2-3 sentences max
- If no meaningful connections exist, say so honestly"""


def _format_news_for_synergy(news: list[dict]) -> str:
    if not news:
        return "No AI news available today."
    lines = []
    for n in news[:5]:
        summary = f" - {n['summary'][:100]}" if n.get("summary") else ""
        lines.append(f"- {n['title']} ({n['source']}){summary}")
    return "\n".join(lines)


def _format_market_for_synergy(market: dict) -> str:
    lines = []
    for idx in market.get("indices", []):
        direction = "up" if idx["change_pct"] >= 0 else "down"
        lines.append(f"- {idx['name']}: {idx['price']:,.0f} ({idx['change_pct']:+.1f}% {direction})")
    for t in market.get("tickers", []):
        direction = "up" if t["change_pct"] >= 0 else "down"
        lines.append(f"- {t['name']}: ${t['price']:,.2f} ({t['change_pct']:+.1f}% {direction})")
    return "\n".join(lines) if lines else "No market data available."


async def generate_synergy_insights(
    news: list[dict],
    market: dict,
    user_insights: str = "",
) -> str:
    """Synthesize AI news + market data + user context into actionable insights."""
    news_block = _format_news_for_synergy(news)
    market_block = _format_market_for_synergy(market)

    if news_block == "No AI news available today." and market_block == "No market data available.":
        return "No strong synergy patterns today."

    user_block = user_insights if user_insights else "No specific user context available."

    user_prompt = (
        f"AI News Today:\n{news_block}\n\n"
        f"Market Data Today:\n{market_block}\n\n"
        f"User's Projects & Interests:\n{user_block}\n\n"
        "Find 2-3 concrete connections between these AI developments and market movements. "
        "Personalize to the user's projects and interests."
    )

    try:
        chat_completion = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYNERGY_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model="moonshotai/kimi-k2-instruct-0905",
            temperature=0.8,
        )
        result = chat_completion.choices[0].message.content
        return result if result and result.strip() else "No strong synergy patterns today."
    except Exception as e:
        logger.error(f"Synergy analysis error: {e}")
        return "Synergy analysis unavailable."
