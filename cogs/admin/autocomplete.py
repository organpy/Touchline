"""Admin autocomplete functions for the single-server, multi-division setup."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


async def _admin_team_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete team codes across every division (single-server setup)."""
    try:
        supabase = get_supabase()
        all_teams = await queries.get_all_teams(supabase)

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
    """Autocomplete season names across every division (single-server setup)."""
    try:
        supabase = get_supabase()
        all_seasons = await queries.get_all_seasons(supabase)

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
    """Autocomplete division names across every league (single-server setup)."""
    try:
        supabase = get_supabase()
        divisions = await queries.get_all_divisions(supabase)

        # Filter to matching names
        current_lower = current.lower()
        filtered = [
            d for d in divisions
            if current_lower in d.get('name', '').lower() or current_lower in (d.get('code') or '').lower()
        ]
        
        # Return as choices (sorted by name, max 25)
        return [
            app_commands.Choice(
                name=f"{d.get('name', 'Unknown')} ({d.get('code', '?')})",
                value=d.get('name', '')
            )
            for d in sorted(filtered, key=lambda x: x.get('name', ''))[:25]
        ]
    except Exception as exc:
        logger.error(f"Error in division autocomplete: {exc}")
        return []
