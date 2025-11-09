"""
Base class for stats cogs with shared helper methods.
All helper methods extracted from StatsCog for reusability.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import discord
from discord.ext import commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


class StatsCogBase(commands.Cog):
    """Base cog with shared helper methods for all stats commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()
        self._league_cache: Dict[str, Dict[str, Any]] = {}
        self._division_cache: Dict[str, Dict[str, Any]] = {}
        self._division_by_league_code: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Shared Helpers
    # ------------------------------------------------------------------

    async def _fetch_league_metadata(self, league_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Resolve a league by code or name and cache the result."""
        if not league_identifier:
            return None

        identifier = league_identifier.strip()
        if not identifier:
            return None

        normalized = identifier.upper()
        if normalized in self._league_cache:
            return self._league_cache[normalized]

        # Since leagues table doesn't exist, create metadata from identifier
        # Assume league_type in divisions/seasons is the code
        league_meta = {'code': normalized, 'name': identifier}
        self._league_cache[normalized] = league_meta
        return league_meta

    async def _find_season(
        self,
        league_input: Optional[str],
        season_input: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Locate a season row and its league metadata."""
        league_meta = await self._fetch_league_metadata(league_input)
        league_code = (league_meta or {}).get('code') if league_meta else None
        division_row: Optional[Dict[str, Any]] = None
        if league_code:
            division_row = await self._fetch_division_by_league_code(league_code)

        def _build_query(active_only: bool = False):
            query = self.supabase.table('seasons').select('*')
            if season_input:
                query = query.ilike('name', f"%{season_input}%")
            if division_row and division_row.get('id'):
                query = query.eq('division_id', division_row['id'])
            if active_only:
                query = query.eq('active', True)
            return query.order('created_at', desc=True).limit(1)

        season_row: Optional[Dict[str, Any]] = None
        try:
            # Prefer active season when none specified explicitly
            result = _build_query(active_only=not season_input).execute()
            season_row = (result.data or [None])[0]
            if not season_row:
                result = _build_query(active_only=False).execute()
                season_row = (result.data or [None])[0]
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch season for league=%s season=%s: %s", league_input, season_input, exc)
            season_row = None

        if not season_row:
            return None, league_meta

        if not league_meta:
            league_meta = await self._resolve_league_for_season(season_row)
        elif not division_row and season_row.get('division_id'):
            await self._fetch_division_by_id(season_row['division_id'])
        return season_row, league_meta

    async def _resolve_league_for_season(self, season: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Derive league metadata from a season row."""
        league_code = season.get('league_type')
        if league_code:
            return await self._fetch_league_metadata(league_code)

        division_id = season.get('division_id')
        if not division_id:
            return None

        division = await self._fetch_division_by_id(division_id)

        if not division:
            return None

        league_code = division.get('league_type')
        league_meta = await self._fetch_league_metadata(league_code)
        if league_meta:
            return league_meta

        return {
            'code': (league_code or '').upper(),
            'name': division.get('name') or league_code,
        }

    async def _fetch_division_by_id(self, division_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch division by ID with caching."""
        if not division_id:
            return None

        cached = self._division_cache.get(division_id)
        if cached:
            return cached

        try:
            response = (
                self.supabase
                .table('divisions')
                .select('id, league_type, name')
                .eq('id', division_id)
                .limit(1)
                .execute()
            )
            division = (response.data or [None])[0]
            if division:
                self._division_cache[division_id] = division
                league_code = (division.get('league_type') or '').upper()
                if league_code:
                    self._division_by_league_code[league_code] = division
            return division
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch division %s: %s", division_id, exc)
            return None

    async def _get_season_by_id(self, season_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch season by ID."""
        if not season_id:
            return None

        try:
            response = (
                self.supabase
                .table('seasons')
                .select('*')
                .eq('id', season_id)
                .limit(1)
                .execute()
            )
            return (response.data or [None])[0]
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch season by id %s: %s", season_id, exc)
            return None

    async def _resolve_season_context(
        self,
        *,
        league_input: Optional[str] = None,
        season_input: Optional[str] = None,
        default_season_id: Optional[str] = None,
        default_league_code: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Resolve season + league metadata with fallbacks for team/player defaults."""

        if season_input or league_input:
            return await self._find_season(league_input, season_input)

        if default_season_id:
            season_row = await self._get_season_by_id(default_season_id)
            league_meta = await self._resolve_league_for_season(season_row) if season_row else None
            if not league_meta and default_league_code:
                league_meta = await self._fetch_league_metadata(default_league_code)
            if season_row or league_meta:
                return season_row, league_meta

        if default_league_code:
            season_row, league_meta = await self._find_season(default_league_code, None)
            if season_row or league_meta:
                return season_row, league_meta

        return await self._find_season(None, None)

    async def _fetch_division_by_league_code(self, league_code: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch division by league code with caching."""
        if not league_code:
            return None

        normalized = league_code.strip().upper()
        if not normalized:
            return None

        cached = self._division_by_league_code.get(normalized)
        if cached:
            return cached

        try:
            response = (
                self.supabase
                .table('divisions')
                .select('id, league_type, name')
                .ilike('league_type', normalized)
                .limit(1)
                .execute()
            )
            division = (response.data or [None])[0]
            if division:
                division_id = division.get('id')
                if division_id:
                    self._division_cache[division_id] = division
                code = (division.get('league_type') or '').upper()
                if code:
                    self._division_by_league_code[code] = division
            return division
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to resolve division for league %s: %s", league_code, exc)
            return None

    async def _resolve_league_for_team(self, team: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Look up the league metadata for a specific team via its division."""
        if not team:
            return None

        league_hint = team.get('league_type')
        if league_hint:
            return await self._fetch_league_metadata(league_hint)

        division_id = team.get('division_id')
        division = await self._fetch_division_by_id(division_id)
        if not division:
            return None

        league_code = division.get('league_type') or division.get('name')
        return await self._fetch_league_metadata(league_code)

    async def _resolve_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Find a player by display name using cache + Supabase fallback."""
        if not player_name:
            return None

        cache = TeamPlayerCache()
        cached = cache.get_player_by_name(player_name)
        if cached:
            return cached

        try:
            player = await queries.get_player_by_name(self.supabase, player_name)
            if player:
                return player
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to resolve player %s: %s", player_name, exc)
        return None

    @staticmethod
    def _select_player_membership(player: Optional[Dict[str, Any]], season_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return the most relevant active membership for a player."""
        if not player:
            return None

        memberships_by_season = player.get('memberships_by_season') or {}
        if season_id and season_id in memberships_by_season:
            return memberships_by_season[season_id]

        active_memberships = player.get('active_memberships') or []
        if active_memberships:
            return active_memberships[0]

        return None

    async def _resolve_league_for_membership(self, membership: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Resolve league metadata based on a player membership entry."""
        if not membership:
            return None

        league_hint = membership.get('league_type')
        if league_hint:
            league_meta = await self._fetch_league_metadata(league_hint)
            if league_meta:
                return league_meta

        division_id = membership.get('division_id')
        if division_id:
            division = await self._fetch_division_by_id(division_id)
            if division:
                league_code = division.get('league_type') or division.get('name')
                return await self._fetch_league_metadata(league_code)

        return None

    @staticmethod
    def _format_league_display(league_meta: Optional[Dict[str, Any]]) -> str:
        """Format league metadata for display."""
        if not league_meta:
            return "Unknown"
        code = (league_meta.get('code') or '').upper()
        name = league_meta.get('name')
        if name and code and name.upper() != code:
            return f"{name} ({code})"
        return name or code or "Unknown"

    async def _validate_player_in_season(self, player: Dict[str, Any], season: Dict[str, Any]) -> bool:
        """Check if player actually has a roster entry in this season."""
        if not player or not season:
            return False
        
        player_id = player.get('id')
        season_id = season.get('id')
        if not player_id or not season_id:
            return False
        
        try:
            response = (
                self.supabase
                .table('rosters')
                .select('id')
                .eq('player_id', player_id)
                .eq('season_id', season_id)
                .limit(1)
                .execute()
            )
            return bool(response.data)
        except Exception as exc:
            logger.error("Failed to validate player in season: %s", exc)
            return False

    async def _validate_team_in_season(self, team: Dict[str, Any], season: Dict[str, Any]) -> bool:
        """Check if team actually plays in this season."""
        if not team or not season:
            return False
        
        team_id = team.get('id')
        season_id = season.get('id')
        team_division = team.get('division_id')
        season_division = season.get('division_id')
        
        if not team_id or not season_id or not team_division:
            return False
        
        # Verify season belongs to team's division
        return team_division == season_division
