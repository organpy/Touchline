"""
Autocomplete functions for stats commands.
All autocomplete handlers are at module level (required by Discord.py).
Uses memoization to cache results and reduce repeated database queries.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from db import get_supabase
from utils.autocomplete_loader import get_metrics, get_team_codes
from utils.team_player_cache import TeamPlayerCache
from utils.caching import season_cache

logger = logging.getLogger(__name__)


def _memoize_autocomplete(ttl_seconds: int = 120):
    """
    Decorator to cache autocomplete results for a TTL period.
    Reduces repeated database queries during rapid autocomplete requests.
    """

    def decorator(func):
        cache = {}
        last_clear = [0]  # Mutable container for timestamp

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            import time

            now = time.time()
            # Clear cache every TTL seconds
            if now - last_clear[0] > ttl_seconds:
                cache.clear()
                last_clear[0] = now

            # Generate cache key from args (skip interaction object)
            try:
                current = kwargs.get("current", "")
                cache_key = (args[0].user.id if args else "unknown", current[:20])
            except:
                cache_key = ("unknown", str(args)[:50])

            if cache_key in cache:
                return cache[cache_key]

            result = await func(*args, **kwargs)
            cache[cache_key] = result
            return result

        return wrapper

    return decorator


@_memoize_autocomplete(ttl_seconds=120)
async def _player_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Return player name suggestions from the TeamPlayerCache (cached)."""
    cache = TeamPlayerCache()
    namespace = getattr(interaction, 'namespace', None)
    team_input = getattr(namespace, 'team', None) if namespace else None

    if team_input:
        # Filter players by team
        team = cache.get_team_by_code(team_input.strip().lower())
        if team:
            players = cache.get_team_players(team['id'])
        else:
            players = []
    else:
        players = cache.get_all_players()

    current_lower = current.lower()
    suggestions = []
    for player in players:
        display = player.get('display_name') or player.get('name')
        if not display:
            continue
        if current_lower and current_lower not in display.lower():
            continue
        suggestions.append(app_commands.Choice(name=display[:100], value=display))
        if len(suggestions) >= 25:
            break

    return suggestions


async def _league_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for league names/codes with 'all' option for cross-league queries."""
    current_lower = current.lower()
    suggestions = []

    # Always include 'all' option at the start if it matches
    if current_lower == '' or 'all'.startswith(current_lower):
        suggestions.append(app_commands.Choice(
            name="all (cross-league rankings)",
            value="all"
        ))

    supabase = get_supabase()
    try:
        # Query distinct league_type from divisions
        response = (
            supabase
            .table('divisions')
            .select('league_type')
            .order('league_type', desc=False)
            .execute()
        )
        divisions = response.data or []
        league_codes = list(set(d.get('league_type') for d in divisions if d.get('league_type')))
    except Exception as exc:  # pragma: no cover - network failure path
        logger.error("Failed to fetch league types for autocomplete: %s", exc)
        league_codes = []

    for code in league_codes:
        label = code.upper()
        if current_lower and current_lower not in label.lower():
            continue
        suggestions.append(app_commands.Choice(name=label, value=code))
        if len(suggestions) >= 25:
            break
    
    return suggestions


async def _season_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for season names (module-level coroutine required by discord)."""
    supabase = get_supabase()
    namespace = getattr(interaction, 'namespace', None)
    league_input = getattr(namespace, 'league', None) if namespace else None

    try:
        query = supabase.table('seasons').select('name, division_id').order('created_at', desc=True).limit(50)
        if league_input:
            league_code = str(league_input).strip().upper()
            division_resp = (
                supabase
                .table('divisions')
                .select('id')
                .ilike('league_type', league_code)
                .limit(1)
                .execute()
            )
            division_row = (division_resp.data or [None])[0]
            if division_row and division_row.get('id'):
                query = query.eq('division_id', division_row['id'])
        response = query.execute()
        seasons = response.data or []
    except Exception as exc:  # pragma: no cover - network failure path
        logger.error("Failed to fetch seasons for autocomplete: %s", exc)
        seasons = []

    current_lower = current.lower()
    suggestions = []
    for season in seasons:
        name = season.get('name') or 'Unknown'
        label = name
        if current_lower and current_lower not in label.lower():
            continue
        suggestions.append(app_commands.Choice(name=label[:100], value=name))
        if len(suggestions) >= 25:
            break
    return suggestions


async def _team_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for team names/codes using centralized team code list."""
    # Get cached teams for name lookups
    cache = TeamPlayerCache()
    teams = cache.get_all_teams()
    
    # Get centralized team codes
    valid_codes = set(get_team_codes())
    
    current_lower = current.lower()
    suggestions = []
    
    for team in teams:
        code = (team.get('code') or '').lower()
        name = team.get('name') or code
        
        # Only include teams that are in the valid code list
        if code not in valid_codes:
            continue
        
        display_code = code.upper()
        label = f"{name} ({display_code})" if name and code and name.upper() != display_code else name or display_code
        if current_lower and current_lower not in label.lower() and current_lower not in code.lower():
            continue
        
        suggestions.append(app_commands.Choice(name=label[:100], value=code))
        if len(suggestions) >= 25:
            break
    
    return suggestions


async def _metric_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for stat metrics (points, assists, goals, etc.)."""
    metrics = get_metrics()
    current_lower = current.lower()

    suggestions = []
    for metric in metrics:
        name = metric.get('name', '')
        if current_lower and current_lower not in name.lower():
            continue
        suggestions.append(app_commands.Choice(
            name=name,
            value=metric.get('value', name)
        ))
        if len(suggestions) >= 25:
            break
    return suggestions
