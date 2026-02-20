"""Token encryption/decryption using Fernet symmetric encryption."""

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from app.core.config import settings
import base64

def get_fernet() -> Fernet:
    """Build a Fernet instance from SECRET_KEY, deriving if needed."""
    try:
        # Try to use the key directly as a valid Fernet key
        return Fernet(settings.SECRET_KEY.encode())
    except Exception:
        # Derive a valid Fernet key from the secret string using PBKDF2HMAC
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"ai-personal-assistant-salt",
            iterations=480_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
        return Fernet(key)

fernet = get_fernet()

def encrypt_token(token: str) -> str | None:
    """Encrypt a plaintext token string."""
    if not token:
        return None
    return fernet.encrypt(token.encode()).decode()

def decrypt_token(token: str) -> str | None:
    """Decrypt a Fernet-encrypted token string."""
    if not token:
        return None
    return fernet.decrypt(token.encode()).decode()
