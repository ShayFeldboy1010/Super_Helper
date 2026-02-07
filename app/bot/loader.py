from aiogram import Bot, Dispatcher
from app.core.config import settings

# Initialize Bot and Dispatcher
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
