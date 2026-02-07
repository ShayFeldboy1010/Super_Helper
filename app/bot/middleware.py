from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from app.core.config import settings

class IDGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user = data.get("event_from_user")
        
        if not user:
            # If we can't identify the user, we should probably allow it to pass 
            # or block it depending on strictness. 
            # For this personal bot, blocking unknown sources is safe unless it's a generic update type.
            return await handler(event, data)

        if user.id != settings.TELEGRAM_USER_ID:
            print(f"access denied for user {user.id} != {settings.TELEGRAM_USER_ID}")
            return
        
        print(f"Authorized access for user {user.id}")
        return await handler(event, data)
