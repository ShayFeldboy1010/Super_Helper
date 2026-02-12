"""Simple in-memory TTL cache for warm Vercel instances."""
import time

_store: dict[str, tuple[object, float]] = {}


def cache_get(key: str) -> object | None:
    """Return cached value if not expired, else None."""
    entry = _store.get(key)
    if entry is None:
        return None
    value, expiry = entry
    if time.time() > expiry:
        _store.pop(key, None)
        return None
    return value


def cache_set(key: str, value: object, ttl_seconds: int) -> None:
    """Store a value with TTL in seconds."""
    _store[key] = (value, time.time() + ttl_seconds)
