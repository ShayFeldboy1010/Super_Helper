from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, types
from app.core.config import settings
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.bot.middleware import IDGuardMiddleware
from app.bot.routers import tasks, auth, google_routes
from app.bot.loader import bot, dp

# Register Middleware
dp.update.outer_middleware(IDGuardMiddleware())

# Register Routers
dp.include_router(google_routes.router)
dp.include_router(tasks.router)



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Set Webhook
    webhook_url = "https://e4b9-80-230-81-108.ngrok-free.app/webhook"
    try:
        print(f"Setting webhook to {webhook_url}...")
        import asyncio
        await asyncio.wait_for(
            bot.set_webhook(
                url=webhook_url,
                secret_token=settings.M_WEBHOOK_SECRET,
                drop_pending_updates=True
            ),
            timeout=5.0
        )
        logger.info(f"Webhook set to {webhook_url}")
        print(f"Webhook set successfully.")
    except Exception as e:
        logger.error(f"Failed to set webhook (timeout/error): {e}")
        print(f"Failed to set webhook (timeout/error): {e}")
    
    yield
    # Shutdown: Remove Webhook
    await bot.delete_webhook()
    logger.info("Webhook removed")

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)
app.include_router(auth.router)

from app.bot.routers import cron
app.include_router(cron.router)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        # Check secret token
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_token != settings.M_WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret")
            return {"status": "unauthorized"}

        update_data = await request.json()
        update = types.Update(**update_data)
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return {"status": "error"}

@app.get("/")
async def root():
    return {"message": "Telegram Command Center is running"}
