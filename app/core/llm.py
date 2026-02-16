"""Centralised LLM wrapper — Gemini 2.5 Flash → Gemini 2.0 Flash → Groq (emergency)."""
import asyncio
import logging
import re
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from groq import AsyncGroq

from app.core.config import settings

logger = logging.getLogger(__name__)

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


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting that breaks Telegram plain text."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # *italic*
    text = re.sub(r'__(.+?)__', r'\1', text)       # __underline__
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headers
    text = re.sub(r'```[\s\S]*?```', '', text)     # ```code blocks```
    text = re.sub(r'`(.+?)`', r'\1', text)         # `inline code`
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)  # [links](url)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)  # > blockquotes
    return text.strip()


def _wrap_gemini_response(response) -> _CompatResponse:
    text = _strip_markdown(response.text or "")
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
            return result
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
    """Call LLM: Gemini 2.5 Flash → Gemini 2.0 Flash → Groq (emergency) → None.

    Returns a ChatCompletion-compatible object or None.
    Caller accesses .choices[0].message.content as before.
    """
    # 1. Try Gemini 2.5 Flash (primary)
    result = await _gemini_call(messages, timeout, temperature, response_format, settings.GEMINI_MODEL)
    if result:
        return result

    # 2. Fallback to Gemini 2.0 Flash
    logger.info("Falling back to Gemini 2.0 Flash...")
    result = await _gemini_call(messages, timeout, temperature, response_format, settings.GEMINI_MODEL_FALLBACK)
    if result:
        return result

    # 3. Emergency fallback to Groq
    logger.info("Emergency fallback to Groq...")
    result = await _groq_call(messages, timeout, temperature, response_format)
    if result:
        return result

    logger.error("All LLM providers failed")
    return None
