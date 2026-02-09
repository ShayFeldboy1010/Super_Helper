import asyncio
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse
from aiogram import types
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



app = FastAPI(title=settings.PROJECT_NAME)
app.include_router(auth.router)

from app.bot.routers import cron
app.include_router(cron.router)

async def _process_update(update_data: dict):
    """Process Telegram update — runs as background task after 200 is sent."""
    try:
        update = types.Update(**update_data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Error processing update: {e}")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_token != settings.M_WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret")
            return JSONResponse({"status": "unauthorized"})

        update_data = await request.json()
        # Return 200 immediately, process in background
        # Vercel keeps the function alive until timeout after response is sent
        return JSONResponse(
            {"status": "ok"},
            background=BackgroundTask(_process_update, update_data),
        )
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        return JSONResponse({"status": "error"})

@app.get("/")
async def root():
    return {"message": "Telegram Command Center is running"}

@app.get("/setup-webhook")
async def setup_webhook():
    """Manual one-time webhook setup. Call once after deploy, not on every boot."""
    try:
        await bot.set_webhook(
            url=settings.WEBHOOK_URL,
            secret_token=settings.M_WEBHOOK_SECRET,
            drop_pending_updates=False,
        )
        return {"status": "ok", "webhook_url": settings.WEBHOOK_URL}
    except Exception as e:
        return {"status": "error", "message": str(e)}
