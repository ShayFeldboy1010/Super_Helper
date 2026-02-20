"""FastAPI application entry point.

Initializes the Telegram bot webhook, registers routers,
and starts the background keep-alive task for Render free tier.
"""

import asyncio
import logging

import httpx
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from app.bot.handler import process_update
from app.bot.loader import bot, dp
from app.bot.middleware import IDGuardMiddleware
from app.bot.routers import tasks, auth, google_routes, cron
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Telegram dispatcher setup ---
dp.update.outer_middleware(IDGuardMiddleware())
dp.include_router(google_routes.router)
dp.include_router(tasks.router)

# --- FastAPI app ---
app = FastAPI(title=settings.PROJECT_NAME)
app.include_router(auth.router)
app.include_router(cron.router)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates and process them in a background task."""
    data = await request.json()
    return JSONResponse({"ok": True}, background=BackgroundTask(process_update, data))


@app.get("/health")
async def health():
    """Health check endpoint (used by cron and monitoring)."""
    return {"status": "ok"}


@app.get("/")
async def root():
    """Root endpoint — confirms the service is running."""
    return {"message": "Telegram Command Center is running"}


@app.get("/setup-webhook")
async def setup_webhook():
    """Manual one-time webhook registration. Call once after deploy."""
    try:
        await bot.set_webhook(
            url=settings.WEBHOOK_URL,
            secret_token=settings.M_WEBHOOK_SECRET,
            drop_pending_updates=False,
        )
        return {"status": "ok", "webhook_url": settings.WEBHOOK_URL}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Keep-alive background task (Render free tier sleeps after 15 min)
# ---------------------------------------------------------------------------

async def _self_ping() -> None:
    """Ping /health every 13 minutes to prevent Render free-tier sleep."""
    await asyncio.sleep(60)
    render_url = settings.RENDER_URL
    if not render_url:
        logger.info("RENDER_URL not set, self-ping disabled")
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"{render_url}/health")
        except Exception:
            pass
        await asyncio.sleep(780)  # 13 minutes


@app.on_event("startup")
async def on_startup():
    """Launch background tasks on server start."""
    asyncio.create_task(_self_ping())
