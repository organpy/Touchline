"""
Base class for stats cogs with shared helper methods.

"League metadata" (``league_meta``) is a division dict from ``LeagueCache`` (``id``, ``code``,
``name``, ``tier``, ``color``...), or None when a command covers every division. Seasons are
league-wide; a season dict gets ``division_id``/``league_type`` attached when a division applies.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from discord.ext import commands

from db import get_supabase, queries
from db.db_utils import execute_async
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


def _with_division(season: Optional[Dict[str, Any]], division: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not season:
        return season
    season = dict(season)
    if division:
        season['division_id'] = division.get('id')
        season['league_type'] = division.get('code')
        season['league_name'] = division.get('name')
    return season


class StatsCogBase(commands.Cog):
    """Base cog with shared helper methods for all stats commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()

    # ------------------------------------------------------------------
    # Divisions and seasons
    # ------------------------------------------------------------------

    async def _fetch_league_metadata(self, league_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Division by code, name or tier ("D1", "Premier", "1")."""
        if not league_identifier or not league_identifier.strip():
            return None
        await LeagueCache.ensure_ready()
        return LeagueCache.find_division(league_identifier)

    async def _fetch_division_by_id(self, division_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not division_id:
            return None
        await LeagueCache.ensure_ready()
        return LeagueCache.division(division_id) or await queries.get_division_by_id(self.supabase, division_id)

    async def _fetch_division_by_league_code(self, league_code: Optional[str]) -> Optional[Dict[str, Any]]:
        return await self._fetch_league_metadata(league_code)

    async def _get_season_by_id(self, season_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not season_id:
            return None
        await LeagueCache.ensure_ready()
        cached = LeagueCache.season(season_id)
        if cached:
            return cached
        try:
            response = await execute_async(self.supabase.table('seasons').select('*').eq('id', season_id).limit(1))
            return (response.data or [None])[0]
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch season by id %s: %s", season_id, exc)
            return None

    async def _pick_season(self, season_input: Optional[str]) -> Optional[Dict[str, Any]]:
        """Season by name/number, else the active season, else the newest one."""
        await LeagueCache.ensure_ready()
        if season_input:
            return LeagueCache.find_season(season_input)
        active = LeagueCache.active_season()
        if active:
            return active
        seasons = LeagueCache.seasons()
        return seasons[0] if seasons else None

    async def _find_season(
        self,
        league_input: Optional[str],
        season_input: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Locate a season and the division the user asked for (if any)."""
        division = None
        if league_input:
            division = await self._fetch_league_metadata(league_input)
            if not division:
                return None, None
        season = await self._pick_season(season_input)
        return _with_division(season, division), division

    async def _resolve_league_for_season(self, season: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The division attached to a season dict, if any."""
        if not season:
            return None
        return await self._fetch_division_by_id(season.get('division_id'))

    async def _resolve_season_context(
        self,
        *,
        league_input: Optional[str] = None,
        season_input: Optional[str] = None,
        default_season_id: Optional[str] = None,
        default_league_code: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Resolve season + division.

        An explicit ``league_input`` wins. Otherwise, with ``team_id`` the division is the team's
        division in the resolved season; else ``default_league_code`` is used for display.
        """
        if league_input:
            return await self._find_season(league_input, season_input)

        if season_input:
            season = await self._pick_season(season_input)
        elif default_season_id:
            season = await self._get_season_by_id(default_season_id) or await self._pick_season(None)
        else:
            season = await self._pick_season(None)
        if not season:
            return None, None

        division = None
        if team_id:
            await LeagueCache.load_season_teams(season['id'])
            division = LeagueCache.team_division(team_id, season['id'])
        elif default_league_code:
            division = await self._fetch_league_metadata(default_league_code)
        return _with_division(season, division), division

    async def _resolve_league_for_team(self, team: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not team:
            return None
        return LeagueCache.team_division(team.get('id')) or await self._fetch_division_by_id(team.get('division_id'))

    async def _resolve_league_for_membership(self, membership: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not membership:
            return None
        if membership.get('team_id'):
            division = LeagueCache.team_division(membership['team_id'], membership.get('season_id'))
            if division:
                return division
        if membership.get('division_id'):
            return await self._fetch_division_by_id(membership['division_id'])
        return await self._fetch_league_metadata(membership.get('league_type'))

    # ------------------------------------------------------------------
    # Players and teams
    # ------------------------------------------------------------------

    async def _resolve_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Find a player by display name using cache + Supabase fallback."""
        if not player_name:
            return None

        cached = TeamPlayerCache.get_player_by_name(player_name)
        if cached:
            return cached

        try:
            player = await queries.get_player_by_name(self.supabase, player_name)
            if player:
                return TeamPlayerCache.get_player(player.get('id')) or player
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to resolve player %s: %s", player_name, exc)
        return None

    @staticmethod
    def _select_player_membership(player: Optional[Dict[str, Any]], season_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The player's team in the active season, when that is the season asked for (or none given).

        A player is on at most one team per season, so the cache keeps a single membership.
        Other seasons are looked up from rosters with ``_player_team_in_season``.
        """
        if not player:
            return None
        membership = player.get('membership')
        if not membership:
            return None
        if season_id and membership.get('season_id') != season_id:
            return None
        return membership

    async def _player_team_in_season(self, player_id: str, season_id: str) -> Optional[str]:
        """Team id the player was (last) rostered on in a season."""
        try:
            response = await execute_async(
                self.supabase.table('rosters').select('team_id, joined_at')
                .eq('player_id', player_id).eq('season_id', season_id)
                .order('joined_at', desc=True).limit(1)
            )
            return response.data[0]['team_id'] if response.data else None
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to look up team for player %s: %s", player_id, exc)
            return None

    async def _player_in_season(
        self, player: Optional[Dict[str, Any]], season: Optional[Dict[str, Any]]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """(was the player on a roster that season, division of the team they played for), from one roster lookup."""
        if not player or not season or not player.get('id') or not season.get('id'):
            return False, None
        team_id = await self._player_team_in_season(player['id'], season['id'])
        if not team_id:
            return False, None
        await LeagueCache.load_season_teams(season['id'])
        return True, LeagueCache.team_division(team_id, season['id'])

    async def _validate_team_in_season(self, team: Dict[str, Any], season: Dict[str, Any]) -> bool:
        """Check the team played in the season (and in the season's division, when one is set)."""
        if not team or not season or not team.get('id') or not season.get('id'):
            return False
        row = await queries.get_team_season_division(self.supabase, season['id'], team['id'])
        if not row:
            return False
        division_id = season.get('division_id')
        return not division_id or row.get('division_id') == division_id

    async def _resolve_team(self, identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        """Team by code or name (cache first)."""
        if not identifier or not identifier.strip():
            return None
        team = TeamPlayerCache.find_team(identifier) or await queries.get_team_by_code(self.supabase, identifier)
        if not team:
            return None
        team = dict(team)
        team.setdefault('display_name', team.get('name'))
        team['league_code'] = team.get('league_type')
        return team

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def _format_league_display(league_meta: Optional[Dict[str, Any]]) -> str:
        """``"Premier (D1)"``; "All divisions" when no division applies."""
        if not league_meta:
            return "All divisions"
        code = (league_meta.get('code') or '').upper()
        name = league_meta.get('name')
        if name and code and name.upper() != code:
            return f"{name} ({code})"
        return name or code or "Unknown"

    @staticmethod
    def _format_league_season_display(
        league_meta: Optional[Dict[str, Any]],
        season_row: Optional[Dict[str, Any]],
    ) -> str:
        league_display = StatsCogBase._format_league_display(league_meta)
        season_name = (season_row or {}).get('name', 'Unknown')
        return f"{league_display} — {season_name}"
