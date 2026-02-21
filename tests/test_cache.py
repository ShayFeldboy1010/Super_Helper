"""Tests for the in-memory TTL cache."""

import time

from app.core.cache import _store, cache_get, cache_set


class TestCache:
    def setup_method(self):
        _store.clear()

    def test_set_and_get(self):
        cache_set("key1", "value1", 60)
        assert cache_get("key1") == "value1"

    def test_missing_key_returns_none(self):
        assert cache_get("nonexistent") is None

    def test_expired_key_returns_none(self):
        cache_set("key2", "value2", 0)
        time.sleep(0.01)
        assert cache_get("key2") is None

    def test_overwrite_value(self):
        cache_set("key3", "old", 60)
        cache_set("key3", "new", 60)
        assert cache_get("key3") == "new"

    def test_stores_any_type(self):
        cache_set("dict", {"a": 1}, 60)
        cache_set("list", [1, 2, 3], 60)
        cache_set("bool", True, 60)
        assert cache_get("dict") == {"a": 1}
        assert cache_get("list") == [1, 2, 3]
        assert cache_get("bool") is True
