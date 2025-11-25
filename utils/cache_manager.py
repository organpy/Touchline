"""
Unified cache manager for all TTL-based caching (seasons, divisions, teams, standings).
Consolidates SeasonCache and TTLCache logic into a single configuration point.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Unified TTL-based cache manager.
    Replaces SeasonCache (singleton) and TTLCache (generic).
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

    # Backward-compatible season cache methods for SeasonCache migration
    _seasons: List[Dict[str, Any]] = []
    _division_map: Dict[str, Dict[str, Any]] = {}
    _last_refresh: Optional[datetime] = None

    CACHE_TTL_SECONDS = 300

    @classmethod
    def is_stale(cls) -> bool:
        """Check if season cache is stale (backward compatibility)."""
        if cls._last_refresh is None:
            return True
        return datetime.now(timezone.utc) - cls._last_refresh > timedelta(seconds=cls.CACHE_TTL_SECONDS)

    @classmethod
    async def ensure_ready(cls) -> List[Dict[str, Any]]:
        """Ensure season cache is populated (backward compatibility)."""
        if not cls._seasons or cls.is_stale():
            await cls.refresh_seasons()
        return cls._seasons

    @classmethod
    async def refresh_seasons(cls, supabase=None) -> List[Dict[str, Any]]:
        """Reload seasons from Supabase (backward compatibility with SeasonCache)."""
        if supabase is not None:
            cls._supabase = supabase

        if not cls._supabase:
            logger.warning("CacheManager refresh requested before Supabase client was set")
            cls._seasons = []
            cls._division_map = {}
            cls._last_refresh = None
            return cls._seasons

        try:
            logger.info("🔄 Refreshing season cache...")
            seasons_response = (
                cls._supabase
                .table("seasons")
                .select("id,name,active,division_id,created_at,start_date,end_date")
                .order("created_at", desc=True)
                .execute()
            )
            seasons = seasons_response.data or []

            division_ids = {s.get("division_id") for s in seasons if s.get("division_id")}
            division_map: Dict[str, Dict[str, Any]] = {}
            if division_ids:
                division_response = (
                    cls._supabase
                    .table("divisions")
                    .select("id,name,league_type")
                    .in_("id", list(division_ids))
                    .execute()
                )
                division_map = {d["id"]: d for d in (division_response.data or []) if d.get("id")}

            for season in seasons:
                division = division_map.get(season.get("division_id"))
                if division:
                    season["division"] = division
                    season.setdefault("league_type", division.get("league_type"))
                    season.setdefault("division_name", division.get("name"))

            cls._seasons = seasons
            cls._division_map = division_map
            cls._last_refresh = datetime.now(timezone.utc)
            logger.info("✅ Season cache refreshed (%d seasons)", len(cls._seasons))
        except Exception as exc:
            logger.error("Failed to refresh season cache: %s", exc, exc_info=True)
            cls._seasons = []

        return cls._seasons

    @classmethod
    def get_cached_seasons(cls) -> List[Dict[str, Any]]:
        """Get cached seasons (backward compatibility)."""
        return cls._seasons

    @classmethod
    def get_season_by_id(cls, season_id: str) -> Optional[Dict[str, Any]]:
        """Get season by ID from cache (backward compatibility)."""
        return next((s for s in cls._seasons if s.get("id") == season_id), None)

    @classmethod
    def get_active_season_for_division(cls, division_id: str) -> Optional[Dict[str, Any]]:
        """Get active season for a division (backward compatibility)."""
        return next(
            (s for s in cls._seasons if s.get("division_id") == division_id and s.get("active")),
            None,
        )
