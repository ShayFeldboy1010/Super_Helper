"""Telegram command handlers — /start, /help.

All message processing is handled by app.bot.handler.process_update() via webhook.
These commands are registered on the aiogram dispatcher for non-webhook fallback.
"""

import logging

from aiogram import Router, types
from aiogram.filters import Command

logger = logging.getLogger(__name__)
router = Router()


HELP_TEXT = (
    "Here's what I can do:\n\n"
    "📅 Reminders & Calendar\n"
    "  - Remind: \"remind me to buy milk tomorrow at 10\"\n"
    "  - Event: \"meeting with John tomorrow at 3pm\"\n"
    "  - Check schedule: \"what do I have on Wednesday?\"\n"
    "  - All tasks/reminders go straight to Google Calendar\n\n"
    "📝 Notes\n"
    "  - Save: \"save: wifi password is 12345\"\n"
    "  - Search: \"what did I save about wifi?\"\n"
    "  - Send a URL to auto-summarize and archive it\n\n"
    "🔍 Info\n"
    "  - AI news: \"what's new in AI?\"\n"
    "  - Market: \"how is NVDA doing?\" / \"market update\"\n"
    "  - Email: \"any new emails?\"\n"
    "  - Web search: \"what is quantum computing?\"\n"
    "  - Synergy: \"any AI-market opportunities?\"\n\n"
    "💬 General\n"
    "  - Chat about anything, ask for advice, brainstorm ideas\n\n"
    "Type /help anytime to see this again."
)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Hey, what's up? I'm here. What do you need?")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(HELP_TEXT)
