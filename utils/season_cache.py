"""In-memory cache for seasons and their division metadata."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SeasonCache:
    """Singleton cache for seasons to power autocomplete and lookups."""

    _instance: Optional["SeasonCache"] = None
    _supabase = None
    _seasons: List[Dict[str, Any]] = []
    _division_map: Dict[str, Dict[str, Any]] = {}
    _last_refresh: Optional[datetime] = None

    CACHE_TTL_SECONDS = 300

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def set_supabase(cls, supabase) -> None:
        """Provide Supabase client for subsequent refreshes."""
        cls._supabase = supabase

    @classmethod
    def is_stale(cls) -> bool:
        if cls._last_refresh is None:
            return True
        return datetime.now(timezone.utc) - cls._last_refresh > timedelta(seconds=cls.CACHE_TTL_SECONDS)

    @classmethod
    async def ensure_ready(cls) -> List[Dict[str, Any]]:
        """Ensure cache is populated (refreshing if stale)."""
        if not cls._seasons or cls.is_stale():
            await cls.refresh()
        return cls._seasons

    @classmethod
    async def refresh(cls, supabase=None) -> List[Dict[str, Any]]:
        """Reload seasons from Supabase."""
        if supabase is not None:
            cls._supabase = supabase

        if not cls._supabase:
            logger.warning("SeasonCache refresh requested before Supabase client was set.")
            cls._seasons = []
            cls._division_map = {}
            cls._last_refresh = None
            return cls._seasons

        try:
            logger.info("🔄 Refreshing SeasonCache...")
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
            logger.info("✅ SeasonCache refreshed (%d seasons)", len(cls._seasons))
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to refresh SeasonCache: %s", exc, exc_info=True)
            cls._seasons = []

        return cls._seasons

    @classmethod
    def get_cached_seasons(cls) -> List[Dict[str, Any]]:
        """Return cached seasons without triggering refresh."""
        return list(cls._seasons)

    @classmethod
    def get_division(cls, division_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not division_id:
            return None
        return cls._division_map.get(division_id)

    @classmethod
    def invalidate(cls) -> None:
        """Clear cached data."""
        cls._seasons = []
        cls._division_map = {}
        cls._last_refresh = None
