from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_USER_ID: int
    SUPABASE_URL: str
    SUPABASE_KEY: str
    GROQ_API_KEY: str
    NVIDIA_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    SECRET_KEY: str = "default-secret-key-change-in-production"
    M_WEBHOOK_SECRET: str
    WEBHOOK_URL: str = "https://super-helper.onrender.com/webhook"
    RENDER_URL: str = ""

    # Google Auth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "https://super-helper.onrender.com/auth/callback"
    
    # Market
    STOCK_WATCHLIST: str = "NVDA,MSFT,GOOGL,META,AAPL"
    STOCK_INDICES: str = "^GSPC,^IXIC,^TA125.TA"

    # Search
    BRAVE_SEARCH_API_KEY: str = ""

    # Proactive Alerts
    ALERT_KEY_CONTACTS: str = ""  # Comma-separated: "boss@co.com,partner@co.com"
    ALERT_URGENT_KEYWORDS: str = "urgent,asap,emergency,critical,deadline,immediately"
    STOCK_ALERT_THRESHOLD: float = 3.0  # % move that triggers alert

    # Defaults
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Telegram Command Center"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

@lru_cache
def get_settings():
    return Settings()

settings = get_settings()
