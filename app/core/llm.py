"""Centralised LLM wrapper — NVIDIA Kimi K2.5 primary, Groq fallback."""
import asyncio
import logging
import re

from openai import AsyncOpenAI
from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

# Primary: NVIDIA Kimi K2.5
_nvidia_client = AsyncOpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=settings.NVIDIA_API_KEY,
) if settings.NVIDIA_API_KEY else None

# Fallback: Groq Kimi K2 Instruct
_groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

NVIDIA_MODEL = "moonshotai/kimi-k2.5"
GROQ_MODEL = "moonshotai/kimi-k2-instruct-0905"

# Strip <think>...</think> blocks from thinking-mode responses
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def strip_thinking(content: str) -> str:
    """Remove thinking blocks from response content."""
    if not content:
        return content
    return _THINK_RE.sub("", content).strip()


async def _call_nvidia(
    messages: list[dict],
    timeout: float,
    temperature: float,
    response_format: dict | None,
    thinking: bool,
) -> object | None:
    """Single attempt on NVIDIA."""
    if not _nvidia_client:
        return None

    kwargs: dict = dict(
        model=NVIDIA_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=4096,
    )
    if response_format:
        kwargs["response_format"] = response_format
    if thinking:
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": True}}
        kwargs["max_tokens"] = 8192  # more room for thinking + answer

    try:
        result = await asyncio.wait_for(
            _nvidia_client.chat.completions.create(**kwargs),
            timeout=timeout,
        )
        return result
    except Exception as e:
        logger.warning(f"NVIDIA call failed: {e}")
        return None


async def _call_groq(
    messages: list[dict],
    timeout: float,
    temperature: float,
    response_format: dict | None,
) -> object | None:
    """Single attempt on Groq (no thinking support)."""
    kwargs: dict = dict(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
    )
    if response_format:
        kwargs["response_format"] = response_format

    try:
        result = await asyncio.wait_for(
            _groq_client.chat.completions.create(**kwargs),
            timeout=timeout,
        )
        return result
    except Exception as e:
        logger.warning(f"Groq call failed: {e}")
        return None


async def llm_call(
    messages: list[dict],
    timeout: float = 7.0,
    temperature: float = 0.7,
    response_format: dict | None = None,
    thinking: bool = False,
) -> object | None:
    """Call LLM with NVIDIA primary + Groq fallback.

    Args:
        thinking: Enable Kimi K2.5 thinking mode for complex reasoning.
                  Only works on NVIDIA. Groq fallback always runs without it.
                  Thinking content is auto-stripped from the response.

    Returns ChatCompletion on success, None on final failure.
    Caller extracts .choices[0].message.content and handles None.
    """
    # If thinking requested, use a tighter timeout to leave room for Groq fallback
    nvidia_timeout = timeout - 1.5 if thinking else timeout

    # 1. Try NVIDIA
    result = await _call_nvidia(messages, nvidia_timeout, temperature, response_format, thinking)
    if result:
        # Strip thinking blocks from content
        if thinking and result.choices and result.choices[0].message.content:
            result.choices[0].message.content = strip_thinking(
                result.choices[0].message.content
            )
        logger.info("NVIDIA call succeeded")
        return result

    # 2. Fallback to Groq (no thinking)
    logger.info("Falling back to Groq")
    result = await _call_groq(messages, timeout, temperature, response_format)
    if result:
        logger.info("Groq fallback succeeded")
        return result

    logger.error("Both NVIDIA and Groq failed")
    return None
