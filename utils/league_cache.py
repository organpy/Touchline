"""In-memory snapshot of the league structure: seasons, divisions, groups and which division each team
plays in (per season).

Most of the bot only needs "the active season", "the divisions in tier order" and "which division is
this team in", many times per command. ``LeagueCache`` loads those once and serves them synchronously.
Call ``LeagueCache.refresh()`` after anything that changes them (the dashboard does this for you).

Division dicts carry ``league_type`` as an alias of ``code`` so display helpers written for the old
fixed leagues keep working with any division.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from db.db_utils import execute_async
from services.division_settings import DivisionSettings

logger = logging.getLogger(__name__)


def _enrich_division(division: Dict[str, Any]) -> Dict[str, Any]:
    division = dict(division)
    division["league_type"] = division.get("code")
    return division


def hex_to_int(value: Optional[str]) -> Optional[int]:
    """``"#1A2B3C"`` -> ``0x1A2B3C`` (None when missing or malformed)."""
    if not value:
        return None
    try:
        return int(str(value).lstrip("#"), 16)
    except (TypeError, ValueError):
        return None


class LeagueCache:
    """Singleton cache of seasons, divisions, groups and season assignments."""

    _supabase = None
    _seasons: List[Dict[str, Any]] = []
    _divisions: Dict[str, Dict[str, Any]] = {}
    _groups: Dict[str, Dict[str, Any]] = {}
    _assignments: Dict[str, Dict[str, Dict[str, Any]]] = {}  # season_id -> team_id -> row
    _last_refresh: Optional[datetime] = None

    CACHE_TTL_SECONDS = 300

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def set_supabase(cls, supabase) -> None:
        cls._supabase = supabase

    @classmethod
    def is_stale(cls) -> bool:
        if cls._last_refresh is None:
            return True
        return datetime.now(timezone.utc) - cls._last_refresh > timedelta(seconds=cls.CACHE_TTL_SECONDS)

    @classmethod
    async def ensure_ready(cls) -> None:
        if cls.is_stale():
            await cls.refresh()

    @classmethod
    async def refresh(cls, supabase=None) -> None:
        """Reload seasons, divisions, groups and the active season's assignments."""
        if supabase is not None:
            cls._supabase = supabase
        if not cls._supabase:
            logger.warning("LeagueCache refresh requested before Supabase client was set")
            return
        try:
            seasons = await execute_async(
                cls._supabase.table("seasons").select("*").order("number", desc=True)
            )
            divisions = await execute_async(cls._supabase.table("divisions").select("*").order("tier"))
            groups = await execute_async(
                cls._supabase.table("division_groups").select("*").order("sort_order")
            )
            cls._seasons = seasons.data or []
            cls._divisions = {d["id"]: _enrich_division(d) for d in (divisions.data or [])}
            cls._groups = {g["id"]: g for g in (groups.data or [])}
            cls._assignments = {}
            active = cls.active_season()
            if active:
                await cls.load_season_teams(active["id"])
            cls._last_refresh = datetime.now(timezone.utc)
            logger.info(
                "✅ LeagueCache refreshed: %d seasons, %d divisions, %d groups",
                len(cls._seasons), len(cls._divisions), len(cls._groups),
            )
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to refresh LeagueCache: %s", exc, exc_info=True)

    @classmethod
    async def load_season_teams(cls, season_id: str) -> Dict[str, Dict[str, Any]]:
        """Load (and cache) every team's division/group for a season."""
        if season_id in cls._assignments:
            return cls._assignments[season_id]
        if not cls._supabase:
            return {}
        response = await execute_async(
            cls._supabase.table("season_teams").select("team_id, division_id, group_id").eq("season_id", season_id)
        )
        mapping = {row["team_id"]: row for row in (response.data or [])}
        cls._assignments[season_id] = mapping
        return mapping

    @classmethod
    def invalidate(cls) -> None:
        cls._seasons = []
        cls._divisions = {}
        cls._groups = {}
        cls._assignments = {}
        cls._last_refresh = None

    @classmethod
    def load_snapshot(
        cls,
        *,
        seasons: List[Dict[str, Any]],
        divisions: List[Dict[str, Any]],
        groups: Optional[List[Dict[str, Any]]] = None,
        assignments: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        """Fill the cache directly (tests and scripts)."""
        cls._seasons = sorted(seasons, key=lambda s: s.get("number") or 0, reverse=True)
        cls._divisions = {d["id"]: _enrich_division(d) for d in divisions}
        cls._groups = {g["id"]: g for g in (groups or [])}
        cls._assignments = {
            season_id: {row["team_id"]: row for row in rows}
            for season_id, rows in (assignments or {}).items()
        }
        cls._last_refresh = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ seasons
    @classmethod
    def active_season(cls) -> Optional[Dict[str, Any]]:
        return next((s for s in cls._seasons if s.get("active")), None)

    @classmethod
    def seasons(cls) -> List[Dict[str, Any]]:
        return list(cls._seasons)

    @classmethod
    def season(cls, season_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not season_id:
            return None
        return next((s for s in cls._seasons if s.get("id") == season_id), None)

    @classmethod
    def find_season(cls, identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Season by exact name, number ("3" or "S3"), or partial name; newest first."""
        if not identifier:
            return None
        text = identifier.strip().lower()
        if not text:
            return None
        for season in cls._seasons:
            if (season.get("name") or "").lower() == text:
                return season
        digits = text[1:] if text.startswith("s") else text
        if digits.isdigit():
            for season in cls._seasons:
                if season.get("number") == int(digits):
                    return season
        for season in cls._seasons:
            if text in (season.get("name") or "").lower():
                return season
        return None

    # ------------------------------------------------------------------ divisions
    @classmethod
    def divisions(cls, include_archived: bool = False) -> List[Dict[str, Any]]:
        rows = [d for d in cls._divisions.values() if include_archived or not d.get("archived_at")]
        return sorted(rows, key=lambda d: (d.get("archived_at") is not None, d.get("tier") or 0))

    @classmethod
    def division(cls, division_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not division_id:
            return None
        return cls._divisions.get(division_id)

    @classmethod
    def find_division(cls, identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Division by code (case-insensitive), name, or tier ("1" / "D1")."""
        if not identifier:
            return None
        text = identifier.strip().lower()
        if not text:
            return None
        for division in cls.divisions(include_archived=True):
            if (division.get("code") or "").lower() == text:
                return division
        for division in cls.divisions(include_archived=True):
            if (division.get("name") or "").lower() == text:
                return division
        digits = text[1:] if text.startswith("d") else text
        if digits.isdigit():
            for division in cls.divisions():
                if division.get("tier") == int(digits):
                    return division
        return None

    @classmethod
    def settings(cls, division_id: Optional[str]) -> DivisionSettings:
        return DivisionSettings.from_division(cls.division(division_id))

    @classmethod
    def division_below(cls, division_id: str) -> Optional[Dict[str, Any]]:
        ordered = cls.divisions()
        for index, division in enumerate(ordered):
            if division["id"] == division_id:
                return ordered[index + 1] if index + 1 < len(ordered) else None
        return None

    @classmethod
    def division_above(cls, division_id: str) -> Optional[Dict[str, Any]]:
        ordered = cls.divisions()
        for index, division in enumerate(ordered):
            if division["id"] == division_id:
                return ordered[index - 1] if index > 0 else None
        return None

    @classmethod
    def top_division(cls) -> Optional[Dict[str, Any]]:
        ordered = cls.divisions()
        return ordered[0] if ordered else None

    @classmethod
    def picks_divisions(cls) -> List[Dict[str, Any]]:
        """Divisions that run a draft."""
        return [d for d in cls.divisions() if cls.settings(d["id"]).picks_enabled]

    # ------------------------------------------------------------------ groups
    @classmethod
    def group(cls, group_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not group_id:
            return None
        return cls._groups.get(group_id)

    @classmethod
    def groups(cls, division_id: str) -> List[Dict[str, Any]]:
        rows = [g for g in cls._groups.values() if g.get("division_id") == division_id]
        return sorted(rows, key=lambda g: (g.get("sort_order") or 0, g.get("name") or ""))

    # ------------------------------------------------------------------ assignments
    @classmethod
    def _season_id(cls, season_id: Optional[str]) -> Optional[str]:
        if season_id:
            return season_id
        active = cls.active_season()
        return active["id"] if active else None

    @classmethod
    def assignment(cls, team_id: Optional[str], season_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The team's division/group row for a season (default: the active season), if loaded."""
        sid = cls._season_id(season_id)
        if not team_id or not sid:
            return None
        return (cls._assignments.get(sid) or {}).get(team_id)

    @classmethod
    def team_division(cls, team_id: Optional[str], season_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        row = cls.assignment(team_id, season_id)
        return cls.division(row["division_id"]) if row else None

    @classmethod
    def team_ids_in_division(cls, division_id: str, season_id: Optional[str] = None) -> List[str]:
        sid = cls._season_id(season_id)
        rows = (cls._assignments.get(sid) or {}).values() if sid else []
        return [r["team_id"] for r in rows if r.get("division_id") == division_id]

    @classmethod
    def assigned_team_ids(cls, season_id: Optional[str] = None) -> List[str]:
        sid = cls._season_id(season_id)
        return list((cls._assignments.get(sid) or {}).keys()) if sid else []

    @classmethod
    def same_division(cls, team_a: str, team_b: str, season_id: Optional[str] = None) -> bool:
        a = cls.assignment(team_a, season_id)
        b = cls.assignment(team_b, season_id)
        return bool(a and b and a["division_id"] == b["division_id"])


_DERIVED_TEAM_KEYS = (
    "division", "division_id", "league_type", "league_name", "group_id", "group_name",
    "active_season_id", "active_season_name",
)


def enrich_team(raw_team: Optional[Dict[str, Any]], season_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Copy of a ``teams`` row with its division/group for a season (default: the active one).

    Adds ``division_id``, ``division``, ``league_type`` (division code), ``league_name``,
    ``group_id``, ``group_name``, ``active_season_id``/``active_season_name`` and
    ``color_primary`` (int) so display code can treat any team the same way.
    """
    if raw_team is None:
        return None
    team = dict(raw_team)
    for key in _DERIVED_TEAM_KEYS:
        team.pop(key, None)
    assignment = LeagueCache.assignment(team.get("id"), season_id)
    if assignment:
        division = LeagueCache.division(assignment.get("division_id")) or {}
        group = LeagueCache.group(assignment.get("group_id")) or {}
        team["division_id"] = assignment.get("division_id")
        team["division"] = division
        team["league_type"] = division.get("code")
        team["league_name"] = division.get("name")
        team["group_id"] = assignment.get("group_id")
        team["group_name"] = group.get("name")
        season = LeagueCache.season(season_id) if season_id else LeagueCache.active_season()
        season = season or {}
        team["active_season_id"] = season.get("id")
        team["active_season_name"] = season.get("name")
    color_val = hex_to_int(team.get("color"))
    if color_val is not None:
        team["color_primary"] = color_val
    return team


def get_league_cache() -> type:
    return LeagueCache
