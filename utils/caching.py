"""
Caching utilities for bot data to reduce redundant queries.
Provides TTL-based in-memory cache for seasons, divisions, and teams.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple TTL (Time-To-Live) based cache with optional max size. Safe to share across threads."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000):
        """
        Initialize cache.

        Args:
            ttl_seconds: How long entries remain valid (default 5 minutes)
            max_size: Maximum number of entries before eviction
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: Dict[str, tuple[Any, datetime]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve item from cache if it exists and hasn't expired.

        Args:
            key: Cache key

        Returns:
            Cached value if found and valid, None otherwise
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            value, expires_at = entry

            if datetime.now() > expires_at:
                # Expired, remove it
                del self._cache[key]
                return None

            return value

    def set(self, key: str, value: Any) -> None:
        """
        Store item in cache with TTL.

        Args:
            key: Cache key
            value: Value to cache
        """
        with self._lock:
            # Simple eviction: if cache is full, clear oldest entries
            if len(self._cache) >= self.max_size:
                # Remove half the entries (oldest first by expiration)
                sorted_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k][1],  # Sort by expiration time
                )
                for remove_key in sorted_keys[: self.max_size // 2]:
                    del self._cache[remove_key]

            expires_at = datetime.now() + timedelta(seconds=self.ttl_seconds)
            self._cache[key] = (value, expires_at)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return current cache size."""
        return len(self._cache)


# Global caches for common data
season_cache = TTLCache(ttl_seconds=300, max_size=100)  # 5 minute TTL
division_cache = TTLCache(ttl_seconds=300, max_size=50)  # 5 minute TTL
team_cache = TTLCache(ttl_seconds=600, max_size=500)  # 10 minute TTL
