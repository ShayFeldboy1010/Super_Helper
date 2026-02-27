"""Centralised LLM wrapper — Gemini 3 Flash → Gemini 2.5 Flash → Groq (emergency)."""
import asyncio
import contextvars
import logging
import re
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tracks which model answered the last llm_call (per async task)
last_model_used: contextvars.ContextVar[str] = contextvars.ContextVar("last_model_used", default="")

# --- Clients ---
_gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
_groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

GROQ_MODEL = "moonshotai/kimi-k2-instruct-0905"


# --- Compatibility wrapper ---
# All callers do `response.choices[0].message.content`.
# We wrap Gemini's response in a compatible shape so callers don't need changes.
@dataclass
class _Message:
    content: str = ""

@dataclass
class _Choice:
    message: _Message = field(default_factory=_Message)

@dataclass
class _CompatResponse:
    """Mimics OpenAI/Groq ChatCompletion shape for callers."""
    choices: list[_Choice] = field(default_factory=lambda: [_Choice()])


def _md_to_telegram_html(text: str) -> str:
    """Convert LLM markdown output to Telegram-safe HTML.

    1. HTML-escape raw text first
    2. Convert supported markdown → Telegram HTML tags
    3. Strip unsupported patterns (code blocks, blockquotes)
    """
    import html as _html

    # Step 1: HTML-escape everything (prevents <script> etc.)
    text = _html.escape(text)

    # Step 2: Strip unsupported patterns
    text = re.sub(r'```[\s\S]*?```', '', text)                  # ```code blocks```
    text = re.sub(r'^&gt;\s?', '', text, flags=re.MULTILINE)    # > blockquotes (already escaped to &gt;)

    # Step 3: Convert markdown → HTML (order matters: bold before italic)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)         # **bold**
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)             # *italic*
    text = re.sub(r'__(.+?)__', r'<u>\1</u>', text)             # __underline__
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)  # # headers
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)         # `inline code`
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)  # [text](url)

    return text.strip()


def _wrap_gemini_response(response) -> _CompatResponse:
    text = _md_to_telegram_html(response.text or "")
    return _CompatResponse(choices=[_Choice(message=_Message(content=text))])


def _wrap_groq_response(response) -> _CompatResponse:
    """Clean Groq response through the same markdown→HTML pipeline."""
    raw = response.choices[0].message.content if response.choices else ""
    text = _md_to_telegram_html(raw)
    return _CompatResponse(choices=[_Choice(message=_Message(content=text))])


def _convert_messages(messages: list[dict]) -> tuple[str | None, str]:
    """Convert OpenAI-style messages to Gemini (system_instruction, contents).

    Returns (system_text, user_text).
    """
    system_parts = []
    content_parts = []

    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_parts.append(text)
        else:
            content_parts.append(text)

    system_text = "\n\n".join(system_parts) if system_parts else None
    user_text = "\n\n".join(content_parts) if content_parts else ""
    return system_text, user_text


async def _gemini_call(
    messages: list[dict],
    timeout: float,
    temperature: float,
    response_format: dict | None,
    model: str,
) -> _CompatResponse | None:
    """Call a Gemini model with 1 retry."""
    system_text, user_text = _convert_messages(messages)

    config_kwargs: dict = {"temperature": temperature}
    if system_text:
        config_kwargs["system_instruction"] = system_text
    if response_format and response_format.get("type") == "json_object":
        config_kwargs["response_mime_type"] = "application/json"

    config = types.GenerateContentConfig(**config_kwargs)

    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                _gemini_client.aio.models.generate_content(
                    model=model,
                    contents=user_text,
                    config=config,
                ),
                timeout=timeout,
            )
            return _wrap_gemini_response(response)
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Gemini ({model}) attempt 1 failed ({e}), retrying in 1s...")
                await asyncio.sleep(1)
            else:
                logger.warning(f"Gemini ({model}) failed after 2 attempts: {e}")
    return None


async def _groq_call(
    messages: list[dict],
    timeout: float,
    temperature: float,
    response_format: dict | None,
) -> object | None:
    """Call Groq as emergency fallback. Returns native ChatCompletion (already compatible)."""
    call_kwargs: dict = dict(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
    )
    if response_format:
        call_kwargs["response_format"] = response_format

    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                _groq_client.chat.completions.create(**call_kwargs),
                timeout=timeout,
            )
            return _wrap_groq_response(result)
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Groq emergency attempt 1 failed ({e}), retrying in 1s...")
                await asyncio.sleep(1)
            else:
                logger.error(f"Groq emergency failed after 2 attempts: {e}")
    return None


async def llm_call(
    messages: list[dict],
    timeout: float = 30.0,
    temperature: float = 0.7,
    response_format: dict | None = None,
    **kwargs,
) -> object | None:
    """Call LLM: Gemini 3 Flash → Gemini 2.5 Flash → Groq (emergency) → None.

    Returns a ChatCompletion-compatible object or None.
    Caller accesses .choices[0].message.content as before.
    """
    # 1. Try Gemini 3 Flash (primary)
    result = await _gemini_call(messages, timeout, temperature, response_format, settings.GEMINI_MODEL)
    if result:
        last_model_used.set(settings.GEMINI_MODEL)
        return result

    # 2. Fallback to Gemini 2.5 Flash
    logger.info("Falling back to Gemini 2.5 Flash...")
    result = await _gemini_call(messages, timeout, temperature, response_format, settings.GEMINI_MODEL_FALLBACK)
    if result:
        last_model_used.set(settings.GEMINI_MODEL_FALLBACK)
        return result

    # 3. Emergency fallback to Groq
    logger.info("Emergency fallback to Groq...")
    result = await _groq_call(messages, timeout, temperature, response_format)
    if result:
        last_model_used.set(GROQ_MODEL)
        return result

    logger.error("All LLM providers failed")
    return None
