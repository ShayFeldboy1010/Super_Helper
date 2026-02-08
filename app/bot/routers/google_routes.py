from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services.google_svc import GoogleService
from app.core.config import settings

router = Router()

@router.message(Command("today"))
async def cmd_today(message: Message):
    status = await message.answer("📅 בודק את היומן שלך...")

    svc = GoogleService(user_id=message.from_user.id)
    events = await svc.get_todays_events()

    response_text = "📅 *לוח הזמנים להיום:*\n\n" + "\n".join(events)
    try:
        await status.edit_text(response_text, parse_mode="Markdown")
    except Exception:
        await status.edit_text(response_text)

@router.message(Command("login"))
async def cmd_login(message: Message):
    base_url = settings.GOOGLE_REDIRECT_URI.replace("/auth/callback", "")
    login_url = f"{base_url}/auth/login"
    await message.answer(
        f"🔗 *חיבור חשבון Google*\nלחץ כאן להתחברות: [התחבר עם Google]({login_url})",
        parse_mode="Markdown"
    )

@router.message(Command("emails"))
async def cmd_emails(message: Message):
    status = await message.answer("📧 מביא את האימיילים האחרונים...")

    svc = GoogleService(user_id=message.from_user.id)
    emails = await svc.get_recent_emails(max_results=5)

    if not emails:
        await status.edit_text("📧 לא נמצאו אימיילים אחרונים (או שחשבון Google לא מחובר).")
        return

    unread = await svc.get_unread_count()

    lines = [f"📧 *אימיילים אחרונים* ({unread} לא נקראו)\n"]
    for e in emails:
        subject = e['subject'].replace('*', '').replace('_', ' ')
        sender = e['from'].replace('*', '').replace('_', ' ')
        snippet = e['snippet'][:80].replace('*', '').replace('_', ' ')
        lines.append(f"• *{subject}*\n  מאת: {sender}\n  {snippet}…\n")

    text = "\n".join(lines)
    try:
        await status.edit_text(text, parse_mode="Markdown")
    except Exception:
        await status.edit_text(text)
