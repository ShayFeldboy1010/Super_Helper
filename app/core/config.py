from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_USER_ID: int
    SUPABASE_URL: str
    SUPABASE_KEY: str
    GROQ_API_KEY: str
    SECRET_KEY: str
    M_WEBHOOK_SECRET: str
    WEBHOOK_URL: str = "https://super-helper-theta.vercel.app/webhook"
    
    # Google Auth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "https://super-helper-theta.vercel.app/auth/callback" 
    
    # Defaults
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Telegram Command Center"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

@lru_cache
def get_settings():
    return Settings()

settings = get_settings()
