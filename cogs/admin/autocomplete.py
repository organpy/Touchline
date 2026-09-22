"""Admin autocomplete functions with proper league/division scoping."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


async def _admin_team_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete team codes, scoped to guild's divisions."""
    try:
        supabase = get_supabase()
        
        # Get divisions for guild
        response = await execute_async(supabase.table('guild_divisions').select(
            'divisions ( id )'
        ).eq('guild_id', interaction.guild_id))
        
        if not response.data:
            return []
        
        division_ids = [item['divisions']['id'] for item in response.data if item.get('divisions')]
        if not division_ids:
            return []
        
        # Get teams from those divisions
        all_teams = []
        for div_id in division_ids:
            teams = await queries.get_all_teams(supabase, div_id)
            all_teams.extend(teams or [])
        
        # Filter to matching codes
        current_upper = current.upper()
        filtered = [
            t for t in all_teams 
            if current_upper in t.get('code', '').upper() or current.lower() in t.get('name', '').lower()
        ]
        
        # Return as choices (sorted by code, max 25)
        return [
            app_commands.Choice(
                name=f"{t.get('code', '?')} - {t.get('name', 'Unknown')}",
                value=t.get('code', '')
            )
            for t in sorted(filtered, key=lambda x: x.get('code', ''))[:25]
        ]
    except Exception as exc:
        logger.error(f"Error in team autocomplete: {exc}")
        return []


async def _admin_player_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete player names (global, not scoped to team)."""
    try:
        cache = TeamPlayerCache()
        all_players = cache.get_all_players()
        
        if not all_players:
            return []
        
        # Filter to matching names
        current_lower = current.lower()
        filtered = [
            p for p in all_players
            if current_lower in p.get('display_name', '').lower()
        ]
        
        # Return as choices (sorted by name, max 25)
        return [
            app_commands.Choice(
                name=p.get('display_name', 'Unknown'),
                value=p.get('id', '')
            )
            for p in sorted(filtered, key=lambda x: x.get('display_name', ''))[:25]
        ]
    except Exception as exc:
        logger.error(f"Error in player autocomplete: {exc}")
        return []


async def _admin_season_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete season names, scoped to guild's divisions."""
    try:
        supabase = get_supabase()
        
        # Get divisions for guild
        response = await execute_async(supabase.table('guild_divisions').select(
            'divisions ( id )'
        ).eq('guild_id', interaction.guild_id))
        
        if not response.data:
            return []
        
        division_ids = [item['divisions']['id'] for item in response.data if item.get('divisions')]
        if not division_ids:
            return []
        
        # Get seasons from those divisions
        all_seasons = []
        for div_id in division_ids:
            response = await execute_async(supabase.table('seasons').select(
                '*'
            ).eq('division_id', div_id))
            all_seasons.extend(response.data or [])
        
        # Filter to matching names
        current_lower = current.lower()
        filtered = [
            s for s in all_seasons
            if current_lower in s.get('name', '').lower()
        ]
        
        # Return as choices (sorted by name, max 25)
        return [
            app_commands.Choice(
                name=s.get('name', 'Unknown'),
                value=s.get('name', '')
            )
            for s in sorted(filtered, key=lambda x: x.get('name', ''))[:25]
        ]
    except Exception as exc:
        logger.error(f"Error in season autocomplete: {exc}")
        return []


async def _admin_division_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete division names, scoped to guild."""
    try:
        supabase = get_supabase()
        
        # Get divisions for guild
        response = await execute_async(supabase.table('guild_divisions').select(
            'divisions ( id, name, league_type )'
        ).eq('guild_id', interaction.guild_id))
        
        if not response.data:
            return []
        
        divisions = [item['divisions'] for item in response.data if item.get('divisions')]
        
        # Filter to matching names
        current_lower = current.lower()
        filtered = [
            d for d in divisions
            if current_lower in d.get('name', '').lower() or current_lower in d.get('league_type', '').lower()
        ]
        
        # Return as choices (sorted by name, max 25)
        return [
            app_commands.Choice(
                name=f"{d.get('name', 'Unknown')} ({d.get('league_type', '?')})",
                value=d.get('name', '')
            )
            for d in sorted(filtered, key=lambda x: x.get('name', ''))[:25]
        ]
    except Exception as exc:
        logger.error(f"Error in division autocomplete: {exc}")
        return []
