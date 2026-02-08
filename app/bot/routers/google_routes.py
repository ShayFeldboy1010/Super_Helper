from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services.google_svc import GoogleService
from app.core.config import settings

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

@router.message(Command("emails"))
async def cmd_emails(message: Message):
    status = await message.answer("📧 Fetching your recent emails...")

    svc = GoogleService(user_id=message.from_user.id)
    emails = await svc.get_recent_emails(max_results=5)

    if not emails:
        await status.edit_text("📧 No recent emails found (or Google account not connected).")
        return

    unread = await svc.get_unread_count()

    lines = [f"📧 **Recent Emails** ({unread} unread)\n"]
    for e in emails:
        lines.append(f"• **{e['subject']}**\n  From: {e['from']}\n  {e['snippet'][:80]}…\n")

    await status.edit_text("\n".join(lines), parse_mode="Markdown")
