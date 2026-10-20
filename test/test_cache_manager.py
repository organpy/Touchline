"""Integration tests for unified CacheManager."""

import pytest
from datetime import datetime, timedelta, timezone

from utils.cache_manager import CacheManager


@pytest.fixture(autouse=True)
def reset_cache_manager():
    """Reset CacheManager state between tests."""
    mgr = CacheManager()
    mgr.clear()
    yield
    mgr.clear()


class TestCacheManagerSingleton:
    """Test CacheManager singleton behavior."""

    def test_singleton_instance(self):
        """Verify CacheManager is a singleton."""
        mgr1 = CacheManager()
        mgr2 = CacheManager()
        assert mgr1 is mgr2


class TestCacheManagerCRUD:
    """Test get/set/clear operations."""

    def test_set_and_get(self):
        """Test basic set/get operations."""
        mgr = CacheManager()
        data = {"id": "season-123", "name": "S10"}
        
        mgr.set("seasons", "key1", data)
        result = mgr.get("seasons", "key1")
        
        assert result == data

    def test_get_nonexistent_key(self):
        """Test getting non-existent key returns None."""
        mgr = CacheManager()
        result = mgr.get("seasons", "nonexistent")
        assert result is None

    def test_get_nonexistent_cache(self):
        """Test getting from non-existent cache returns None."""
        mgr = CacheManager()
        result = mgr.get("invalid_cache", "key1")
        assert result is None

    def test_clear_specific_cache(self):
        """Test clearing a specific cache."""
        mgr = CacheManager()
        
        mgr.set("seasons", "key1", {"data": "test"})
        mgr.set("teams", "key1", {"data": "test"})
        
        mgr.clear("seasons")
        
        assert mgr.get("seasons", "key1") is None
        assert mgr.get("teams", "key1") is not None

    def test_clear_all_caches(self):
        """Test clearing all caches."""
        mgr = CacheManager()
        
        mgr.set("seasons", "key1", {"data": "test"})
        mgr.set("teams", "key1", {"data": "test"})
        mgr.set("divisions", "key1", {"data": "test"})
        
        mgr.clear()
        
        assert mgr.get("seasons", "key1") is None
        assert mgr.get("teams", "key1") is None
        assert mgr.get("divisions", "key1") is None

    def test_set_overwrites_existing(self):
        """Test that set overwrites existing values."""
        mgr = CacheManager()
        
        mgr.set("seasons", "key1", {"version": 1})
        mgr.set("seasons", "key1", {"version": 2})
        
        result = mgr.get("seasons", "key1")
        assert result["version"] == 2


class TestCacheManagerTTL:
    """Test TTL and expiration logic."""

    def test_entry_expires_after_ttl(self):
        """Test that entries expire after TTL."""
        mgr = CacheManager()
        mgr.set("seasons", "key1", {"data": "test"})
        
        # Manually age the entry past TTL
        cache = mgr._caches["seasons"]
        value, expires = cache["key1"]
        cache["key1"] = (value, datetime.now(timezone.utc) - timedelta(seconds=1))
        
        # Should be expired now
        result = mgr.get("seasons", "key1")
        assert result is None
        # Entry should be removed
        assert "key1" not in cache

    def test_entry_valid_before_ttl(self):
        """Test that entries are valid before TTL."""
        mgr = CacheManager()
        mgr.set("seasons", "key1", {"data": "test"})
        
        # Check immediately
        result = mgr.get("seasons", "key1")
        assert result == {"data": "test"}

    def test_different_ttl_per_cache_type(self):
        """Test that different cache types have different TTLs."""
        config = CacheManager._config
        
        # Standings should have shorter TTL than teams
        assert config["standings"]["ttl"] < config["teams"]["ttl"]
        assert config["standings"]["ttl"] == 120
        assert config["teams"]["ttl"] == 600


class TestCacheManagerCapacity:
    """Test max size limits and eviction."""

    def test_respects_max_size(self):
        """Test that cache respects max_size limit."""
        mgr = CacheManager()
        max_size = CacheManager._config["seasons"]["max_size"]
        
        # Add max_size + 10 entries
        for i in range(max_size + 10):
            mgr.set("seasons", f"key{i}", {"id": i})
        
        # Cache should not exceed capacity
        cache_size = mgr.size("seasons")
        assert cache_size <= max_size

    def test_size_method(self):
        """Test size() method returns correct count."""
        mgr = CacheManager()
        
        mgr.set("seasons", "key1", {"data": "test"})
        mgr.set("seasons", "key2", {"data": "test"})
        mgr.set("seasons", "key3", {"data": "test"})
        
        assert mgr.size("seasons") == 3

    def test_all_sizes(self):
        """Test all_sizes() returns dict of cache sizes."""
        mgr = CacheManager()
        
        mgr.set("seasons", "key1", {"data": "test"})
        mgr.set("teams", "key1", {"data": "test"})
        mgr.set("teams", "key2", {"data": "test"})
        
        sizes = mgr.all_sizes()
        
        assert sizes["seasons"] == 1
        assert sizes["teams"] == 2
        assert sizes["divisions"] == 0
