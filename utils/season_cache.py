"""Season lookups for autocomplete. Seasons are league-wide; this wraps ``LeagueCache``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.league_cache import LeagueCache


class SeasonCache:
    """Thin facade over LeagueCache kept for season autocomplete callers."""

    @classmethod
    def set_supabase(cls, supabase) -> None:
        LeagueCache.set_supabase(supabase)

    @classmethod
    def is_stale(cls) -> bool:
        return LeagueCache.is_stale()

    @classmethod
    async def ensure_ready(cls) -> List[Dict[str, Any]]:
        await LeagueCache.ensure_ready()
        return LeagueCache.seasons()

    @classmethod
    async def refresh(cls, supabase=None) -> List[Dict[str, Any]]:
        await LeagueCache.refresh(supabase)
        return LeagueCache.seasons()

    @classmethod
    def get_cached_seasons(cls) -> List[Dict[str, Any]]:
        return LeagueCache.seasons()

    @classmethod
    def get_division(cls, division_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return LeagueCache.division(division_id)

    @classmethod
    def invalidate(cls) -> None:
        LeagueCache.invalidate()
