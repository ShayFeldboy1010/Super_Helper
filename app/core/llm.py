"""Centralised LLM wrapper with retry + timeout."""
import asyncio
import logging

from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

MODEL = "moonshotai/kimi-k2-instruct-0905"


async def llm_call(
    messages: list[dict],
    timeout: float = 8.0,
    temperature: float = 0.7,
    response_format: dict | None = None,
    model: str = MODEL,
) -> object | None:
    """Call Groq with 1 retry + configurable timeout.

    Returns the ChatCompletion on success, None on final failure.
    Caller is responsible for extracting .choices[0].message.content
    and handling the None case with a fallback.
    """
    kwargs: dict = dict(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    if response_format:
        kwargs["response_format"] = response_format

    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                _client.chat.completions.create(**kwargs),
                timeout=timeout,
            )
            return result
        except Exception as e:
            if attempt == 0:
                logger.warning(f"LLM call attempt 1 failed ({e}), retrying in 1s...")
                await asyncio.sleep(1)
            else:
                logger.error(f"LLM call failed after 2 attempts: {e}")
    return None
