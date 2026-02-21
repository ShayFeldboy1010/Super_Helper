import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.database import supabase
from app.core.security import encrypt_token

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

@router.get("/login")
async def login():
    return RedirectResponse(
        f"https://accounts.google.com/o/oauth2/v2/auth?client_id={settings.GOOGLE_CLIENT_ID}&response_type=code&scope=https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/gmail.readonly&redirect_uri={settings.GOOGLE_REDIRECT_URI}&access_type=offline&prompt=consent"
    )

@router.get("/callback")
async def callback(code: str, request: Request):
    async with httpx.AsyncClient() as client:
        # Exchange code for token
        data = {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
        resp = await client.post("https://oauth2.googleapis.com/token", data=data)
        if resp.status_code != 200:
            logger.error(f"Google Auth failed: {resp.text}")
            raise HTTPException(status_code=400, detail="Auth failed")
        
        token_data = resp.json()
        refresh_token = token_data.get("refresh_token")
        
        if not refresh_token:
            # We might already have it if the user didn't revoke permissions.
            # Ideally we want to force re-consent if missing, but for personal bot it's okay.
            pass
        
        # Encrypt and Store
        encrypted_rt = encrypt_token(refresh_token) if refresh_token else None
        
        # Update User in Supabase
        # We assume the Telegram ID is known or we need a way to link it.
        # This is tricky without session state.
        # For a Single User bot, we can just update the *only* user matching our ID.
        
        if encrypted_rt:
            try:
                supabase.table("users").upsert({
                    "telegram_id": settings.TELEGRAM_USER_ID,
                    "google_refresh_token": encrypted_rt
                }).execute()
            except Exception as e:
                logger.error(f"Failed to save token: {e}")
                return {"error": "Database error"}
        
        return {"message": "Google Account Connected Successfully! You can close this window."}
