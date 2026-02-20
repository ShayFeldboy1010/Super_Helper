"""Telegram user authorization middleware."""

import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from app.core.config import settings

logger = logging.getLogger(__name__)


class IDGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Allow only the configured TELEGRAM_USER_ID to interact with the bot."""
        user = data.get("event_from_user")

        if not user:
            return await handler(event, data)

        if user.id != settings.TELEGRAM_USER_ID:
            logger.warning(f"Access denied for user {user.id}")
            return

        return await handler(event, data)
