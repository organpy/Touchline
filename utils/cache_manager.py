"""
Unified cache manager for TTL-based caching (seasons, divisions, teams, standings).
The league structure itself (seasons, divisions, assignments) lives in utils.league_cache.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Unified TTL-based cache manager.
    """

    # Configuration (tunable for different data types)
    _config = {
        "seasons": {"ttl": 300, "max_size": 100},  # 5 minutes
        "divisions": {"ttl": 300, "max_size": 50},  # 5 minutes
        "teams": {"ttl": 600, "max_size": 500},  # 10 minutes
        "standings": {"ttl": 120, "max_size": 50},  # 2 minutes
    }

    _instance: Optional["CacheManager"] = None
    _supabase = None
    _caches: Dict[str, Dict[str, tuple[Any, datetime]]] = {
        name: {} for name in _config.keys()
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def set_supabase(cls, supabase) -> None:
        """Provide Supabase client for subsequent refreshes."""
        cls._supabase = supabase

    @classmethod
    def get(cls, cache_name: str, key: str) -> Optional[Any]:
        """
        Retrieve item from named cache if it exists and hasn't expired.

        Args:
            cache_name: Cache namespace (e.g., 'seasons', 'teams')
            key: Item key within that cache

        Returns:
            Cached value if found and valid, None otherwise
        """
        if cache_name not in cls._caches:
            logger.warning("Cache '%s' not found", cache_name)
            return None

        cache = cls._caches[cache_name]
        if key not in cache:
            return None

        value, expires_at = cache[key]
        if datetime.now(timezone.utc) > expires_at:
            del cache[key]
            return None

        return value

    @classmethod
    def set(cls, cache_name: str, key: str, value: Any) -> None:
        """
        Store item in named cache with TTL.

        Args:
            cache_name: Cache namespace
            key: Item key
            value: Value to cache
        """
        if cache_name not in cls._caches:
            logger.warning("Cache '%s' not found", cache_name)
            return

        cache = cls._caches[cache_name]
        config = cls._config[cache_name]

        # Evict oldest if at capacity
        if len(cache) >= config["max_size"]:
            oldest_key = min(cache.keys(), key=lambda k: cache[k][1])
            del cache[oldest_key]

        ttl = config["ttl"]
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        cache[key] = (value, expires_at)

    @classmethod
    def clear(cls, cache_name: Optional[str] = None) -> None:
        """
        Clear cache(s).

        Args:
            cache_name: If provided, clear only that cache. Otherwise clear all.
        """
        if cache_name:
            if cache_name in cls._caches:
                cls._caches[cache_name].clear()
        else:
            for cache in cls._caches.values():
                cache.clear()

    @classmethod
    def size(cls, cache_name: str) -> int:
        """Return current size of named cache."""
        if cache_name not in cls._caches:
            return 0
        return len(cls._caches[cache_name])

    @classmethod
    def all_sizes(cls) -> Dict[str, int]:
        """Return sizes of all caches."""
        return {name: len(cache) for name, cache in cls._caches.items()}
