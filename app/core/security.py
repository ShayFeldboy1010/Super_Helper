from cryptography.fernet import Fernet
from app.core.config import settings
import base64

# Ensure SECRET_KEY is valid for Fernet (32 url-safe base64-encoded bytes)
# If the user provided a simple string, we hash/pad it. 
# For simplicity here, we assume the user will eventually provide a valid key 
# OR we generate one deterministically if needed.
# Ideally, we'd use a proper KDF.
# Let's trust the dev env for now or handle the error gracefully.

def get_fernet():
    try:
        # Try to use the key directly
        return Fernet(settings.SECRET_KEY.encode())
    except Exception:
        # Fallback: Generate a key from the secret string (not production safe but works for now)
        # In a real app, use PBKDF2HMAC
        # For this personal project, let's just warn and use a dummy if invalid, 
        # or require the user to fix it.
        # Actually, let's make a simple consistent key from the string provided.
        # This is A HACK for ease of use.
        import hashlib
        key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

fernet = get_fernet()

def encrypt_token(token: str) -> str:
    if not token:
        return None
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(token: str) -> str:
    if not token:
        return None
    return fernet.decrypt(token.encode()).decode()
