"""
Tests for caching utilities (TTL cache, season cache, etc.)
"""

import pytest
import time
from unittest.mock import Mock

from utils.caching import TTLCache, season_cache, division_cache, team_cache


class TestTTLCache:
    """Test TTL cache functionality."""

    def test_cache_stores_and_retrieves_value(self):
        """Test basic set/get operations."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("key1", {"data": "value"})
        
        assert cache.get("key1") == {"data": "value"}

    def test_cache_returns_none_for_missing_key(self):
        """Test get returns None for non-existent keys."""
        cache = TTLCache()
        assert cache.get("nonexistent") is None

    def test_cache_expires_after_ttl(self):
        """Test that entries expire after TTL."""
        cache = TTLCache(ttl_seconds=1)  # 1 second TTL
        cache.set("key1", "value1")
        
        # Should exist immediately
        assert cache.get("key1") == "value1"
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Should be expired
        assert cache.get("key1") is None

    def test_cache_respects_max_size(self):
        """Test that cache evicts when max_size reached."""
        cache = TTLCache(ttl_seconds=60, max_size=3)
        
        # Fill cache to capacity
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        assert cache.size() == 3
        
        # Add one more (should trigger eviction)
        cache.set("key4", "value4")
        
        # Cache should still have entries but some old ones removed
        assert cache.size() <= 3

    def test_cache_clear(self):
        """Test clearing all cache entries."""
        cache = TTLCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        assert cache.size() == 2
        cache.clear()
        assert cache.size() == 0
        assert cache.get("key1") is None

    def test_global_season_cache_exists(self):
        """Test that global season_cache is initialized."""
        assert season_cache is not None
        assert isinstance(season_cache, TTLCache)

    def test_global_caches_have_correct_ttl(self):
        """Test that global caches have appropriate TTLs."""
        # Season cache should be 5 minutes (300 seconds)
        assert season_cache.ttl_seconds == 300
        # Team cache should be 10 minutes (600 seconds)
        assert team_cache.ttl_seconds == 600

    def test_concurrent_cache_operations(self):
        """Test that cache handles concurrent-like operations."""
        cache = TTLCache(ttl_seconds=60)
        
        # Simulate multiple "concurrent" sets
        for i in range(100):
            cache.set(f"key{i}", f"value{i}")
        
        # All should be retrievable
        for i in range(100):
            assert cache.get(f"key{i}") == f"value{i}"


class TestCacheIntegration:
    """Integration tests for cache usage patterns."""

    def test_cache_with_complex_objects(self):
        """Test caching complex nested objects."""
        cache = TTLCache(ttl_seconds=60)
        complex_obj = {
            "id": "uuid-123",
            "name": "Test Team",
            "players": [
                {"id": "player-1", "name": "Alice"},
                {"id": "player-2", "name": "Bob"},
            ],
            "metadata": {"league": "NBL", "active": True},
        }
        
        cache.set("team_pandas", complex_obj)
        retrieved = cache.get("team_pandas")
        
        assert retrieved == complex_obj
        assert retrieved["players"][0]["name"] == "Alice"

    def test_cache_overwrites_existing_key(self):
        """Test that setting an existing key updates the value."""
        cache = TTLCache(ttl_seconds=60)
        
        cache.set("key1", "old_value")
        assert cache.get("key1") == "old_value"
        
        cache.set("key1", "new_value")
        assert cache.get("key1") == "new_value"

    def test_cache_with_none_value(self):
        """Test that cache can store None values."""
        cache = TTLCache(ttl_seconds=60)
        
        cache.set("empty", None)
        # Should return None, not distinguish from missing key
        # This is intentional—None is a valid cached value
        assert cache.get("empty") is None


def test_ttl_cache_is_safe_under_concurrent_access():
    """Concurrent get/set/expiry from worker threads must never raise."""
    import threading
    from utils.caching import TTLCache

    cache = TTLCache(ttl_seconds=0, max_size=8)  # every entry is already expired on read
    errors = []

    def hammer(worker: int):
        try:
            for i in range(2000):
                key = f"k{(worker + i) % 12}"
                cache.set(key, i)
                cache.get(key)
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
