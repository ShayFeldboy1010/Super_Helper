from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services.google_svc import GoogleService

router = Router()

@router.message(Command("today"))
async def cmd_today(message: Message):
    status = await message.answer("📅 Checking your calendar...")
    
    svc = GoogleService(user_id=message.from_user.id)
    events = await svc.get_todays_events()
    
    response_text = "📅 **Today's Schedule:**\n\n" + "\n".join(events)
    await status.edit_text(response_text, parse_mode="Markdown")

@router.message(Command("login"))
async def cmd_login(message: Message):
    # Construct the login URL based on the configured Redirect URI base
    base_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "")
    login_url = f"{base_url}/auth/login" 
    await message.answer(
        f"🔗 **Connect Google Account**\nTap here to authorize: [Login via Google]({login_url})",
        parse_mode="Markdown"
    )
