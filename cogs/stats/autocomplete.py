"""
Autocomplete functions for stats commands.
All autocomplete handlers are at module level (required by Discord.py).
Delegates to AutocompleteHelper for centralized, DRY logic.
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
from utils.autocomplete_helper import AutocompleteHelper

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

            # Generate cache key from what the user typed; discord.py passes (interaction, current)
            # positionally, and the team filter changes the suggestions too.
            try:
                interaction = args[0]
                current = args[1] if len(args) > 1 else kwargs.get("current", "")
                namespace = getattr(interaction, 'namespace', None)
                team_input = getattr(namespace, 'team', None) if namespace else None
                cache_key = (interaction.user.id, (current or "")[:20], team_input)
            except (AttributeError, IndexError, TypeError):
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
    """Return player name suggestions (delegates to AutocompleteHelper)."""
    namespace = getattr(interaction, 'namespace', None)
    team_input = getattr(namespace, 'team', None) if namespace else None
    return await AutocompleteHelper.player_name_choices(current, team_input)


async def _league_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for league names/codes (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.league_choices(current)


async def _season_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for season names (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.season_name_choices(current)


async def _team_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for team names/codes (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.team_code_choices(current)


async def _metric_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for stat metrics (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.metric_choices(current)


async def _limit_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[int]]:
    """Autocomplete for limit parameters (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.limit_choices(current)
