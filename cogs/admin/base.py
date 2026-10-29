"""Admin transactions base class with shared helpers for team/roster/picks management."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import discord
from discord.ext import commands

from db import get_supabase, queries
from utils.league_cache import LeagueCache, enrich_team
from utils.permissions import AdminOnlyView
from utils.team_player_cache import TeamPlayerCache
from utils.config_manager import get_config_manager
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


class AdminBaseManager:
    """Base manager class for admin operations with shared helpers.
    
    Provides:
    - League/division context resolution
    - Team resolution with league scoping
    - Season context with validation
    - Consistent error messages and formatting
    """

    def __init__(self):
        self.supabase = get_supabase()
        self.config = get_config_manager()

    # =====================================================================
    # Context Resolution Methods
    # =====================================================================

    async def get_guild_divisions(self, guild_id: int) -> list[Dict[str, Any]]:
        """Divisions in tier order (guild_id ignored - single server setup)."""
        return await queries.get_all_divisions(self.supabase)

    async def get_active_league_for_guild(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """The top division (tier 1), if any."""
        divisions = await self.get_guild_divisions(guild_id)
        return divisions[0] if divisions else None

    async def resolve_team_in_guild(
        self,
        team_code_or_name: str,
        guild_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a team by code or name (cache first, then the database)."""
        search_term = (team_code_or_name or '').strip()
        if not search_term:
            return None
        cached = TeamPlayerCache.find_team(search_term)
        if cached:
            return cached
        team = await queries.get_team_by_code(self.supabase, search_term)
        if team:
            return team
        try:
            response = await execute_async(
                self.supabase.table('teams').select('*')
                .ilike('name', f"%{search_term.replace('-', ' ')}%")
                .is_('archived_at', 'null').limit(1)
            )
            return enrich_team(response.data[0]) if response.data else None
        except Exception as exc:
            logger.error(f"Error resolving team: {exc}")
            return None

    async def get_teams_for_guild(self, guild_id: int) -> list[Dict[str, Any]]:
        """Every active team, enriched with its division this season."""
        return await queries.get_all_teams(self.supabase)

    # =====================================================================
    # Season Context Methods
    # =====================================================================

    async def get_active_season_for_guild(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """The league's active season."""
        return await queries.get_active_season(self.supabase)

    async def resolve_season_context(
        self,
        guild_id: int,
        season_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Season by name/number, or the active season."""
        await LeagueCache.ensure_ready()
        if season_name:
            return LeagueCache.find_season(season_name)
        return await self.get_active_season_for_guild(guild_id)

    # =====================================================================
    # Validation Methods
    # =====================================================================

    async def validate_team_in_guild(
        self,
        team_id: str,
        guild_id: int,
    ) -> bool:
        """True when the team exists and isn't archived."""
        team = TeamPlayerCache.get_team(team_id)
        return bool(team and not team.get('archived_at'))

    async def validate_player_in_season(
        self,
        player_id: str,
        season_id: str,
    ) -> bool:
        """Check if a player is on an active roster in the season."""
        try:
            response = await execute_async(self.supabase.table('rosters').select(
                'id'
            ).eq('player_id', player_id).eq('season_id', season_id).is_('left_at', 'null').limit(1))
            return len(response.data or []) > 0
        except Exception as exc:
            logger.error(f"Failed to validate player in season: {exc}")
            return False

    # =====================================================================
    # Formatting Methods
    # =====================================================================

    def format_division_display(self, division: Optional[Dict[str, Any]]) -> str:
        """Format division for display."""
        if not division:
            return "Unknown Division"
        
        code = (division.get('code') or division.get('league_type') or '').upper()
        name = division.get('name', '').strip()
        
        if name and code and name.upper() != code:
            return f"**{name}** ({code})"
        elif name:
            return f"**{name}**"
        else:
            return f"**{code}**" if code else "Unknown"

    def format_team_display(self, team: Optional[Dict[str, Any]]) -> str:
        """Format team for display with code and name."""
        if not team:
            return "Unknown Team"
        
        code = team.get('code', '?')
        name = team.get('name', 'Unknown')
        emoji = team.get('emoji', '🏀')
        
        return f"{emoji} **{name}** (`{code}`)"

    def format_season_display(self, season: Optional[Dict[str, Any]]) -> str:
        """Format season for display."""
        if not season:
            return "Unknown Season"
        
        return f"**{season.get('name', 'Unknown')} Season**"

    # =====================================================================
    # Error Message Helpers
    # =====================================================================

    @staticmethod
    def error_team_not_found(team_ref: str) -> str:
        """Error message for team not found."""
        return f"❌ Team `{team_ref}` not found in this server's divisions."

    @staticmethod
    def error_team_not_in_guild(team_name: str) -> str:
        """Error message for team not found."""
        return f"❌ Team **{team_name}** was not found."

    @staticmethod
    def error_season_not_found(season_name: str) -> str:
        """Error message for season not found."""
        return f"❌ Season `{season_name}` not found."

    @staticmethod
    def error_no_active_season() -> str:
        """Error message for no active season."""
        return "⚠️ No active season configured for this server."

    @staticmethod
    def error_player_not_found(player_ref: str) -> str:
        """Error message for player not found."""
        return f"❌ Player `{player_ref}` not found."

    @staticmethod
    def error_player_not_in_season() -> str:
        """Error message for player not in season."""
        return "❌ Player is not on any roster in this season."


class AdminUIView(AdminOnlyView):
    """Base view for admin UI with shared initialization."""

    def __init__(self, manager: AdminBaseManager, guild_id: int, *, timeout: Optional[float] = 300):
        super().__init__(timeout=timeout)
        self.manager = manager
        self.guild_id = guild_id
        self.original_message: Optional[discord.Message] = None

    async def update_display(self) -> None:
        """Refresh the embed message. Override in subclasses."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:
            logger.error(f"Failed to update admin UI: {exc}")

    def create_embed(self) -> discord.Embed:
        """Create embed for this view. Must be overridden in subclasses."""
        raise NotImplementedError("Subclasses must implement create_embed()")
