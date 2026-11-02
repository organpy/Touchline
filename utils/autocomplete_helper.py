"""
AutocompleteHelper: Centralized, reusable autocomplete functions for stats and transactions.
Provides single source of truth for metric choices, team choices, player choices.
Metrics and limits come from autocomplete_data.json; teams, divisions and seasons from the
database caches.
"""

import logging
from typing import List, Optional

import discord
from discord import app_commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
from utils.autocomplete_loader import load_autocomplete_data, get_league_entries
from utils.team_data_provider import TeamDataProvider
from utils.season_cache import SeasonCache
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)


class AutocompleteHelper:
    """Centralized autocomplete helpers for all commands."""

    # ==================== METRICS ====================

    @staticmethod
    async def metric_choices(current: str) -> List[app_commands.Choice[str]]:
        """
        Get metric choices for leaderboard/stats commands.
        Uses autocomplete_data.json for single source of truth.
        """
        data = load_autocomplete_data()
        metrics = data.get("metrics", [])

        current_lower = current.lower()
        filtered = [
            m for m in metrics
            if current_lower in m.get("name", "").lower()
            or current_lower in m.get("value", "").lower()
        ]

        return [
            app_commands.Choice(name=m.get("name", ""), value=m.get("value", ""))
            for m in filtered[:25]
        ]

    @staticmethod
    async def limit_choices(current: str) -> List[app_commands.Choice[int]]:
        """Get limit/result count choices (3, 5, 10, 15, 20, 25) as integers."""
        data = load_autocomplete_data()
        limits = data.get("limits", [])

        current_lower = current.lower()
        filtered = [
            l for l in limits
            if current_lower in l.get("name", "")
        ]

        choices: List[app_commands.Choice[int]] = []
        for item in filtered[:25]:
            raw_value = item.get("value") or item.get("name")
            try:
                int_value = int(str(raw_value))
            except (TypeError, ValueError):
                continue

            choices.append(
                app_commands.Choice(
                    name=str(int_value),
                    value=int_value,
                )
            )

        return choices

    # ==================== TEAMS ====================

    @staticmethod
    async def team_code_choices(current: str) -> List[app_commands.Choice[str]]:
        """
        Get team code choices with display names, e.g. "Frost Falcons".
        """
        provider = TeamDataProvider()
        all_teams = provider.get_all_teams()

        current_lower = current.lower()
        filtered = [
            team for team in all_teams
            if current_lower in team["code"].lower()
            or current_lower in team["display_name"].lower()
        ]

        return [
            app_commands.Choice(
                name=provider.format_team_with_emoji(team["code"])[:100],
                value=team["code"].lower()
            )
            for team in filtered[:25]
        ]

    @staticmethod
    async def team_region_choices(current: str) -> List[app_commands.Choice[str]]:
        """Get division code choices."""
        provider = TeamDataProvider()
        regions = provider.get_regions()

        current_lower = current.lower()
        filtered = [r for r in regions if current_lower in r.lower()]

        return [
            app_commands.Choice(name=region, value=region)
            for region in filtered[:25]
        ]

    # ==================== PLAYERS ====================

    @staticmethod
    async def player_name_choices(
        current: str,
        team_code: Optional[str] = None,
    ) -> List[app_commands.Choice[str]]:
        """
        Get player name suggestions.
        If team_code provided, filter to that team.
        """
        cache = TeamPlayerCache()

        if team_code:
            # Filter by team
            team = cache.get_team_by_code(team_code.strip().lower())
            if team:
                players = cache.get_team_players(team["id"])
            else:
                players = []
        else:
            players = cache.get_all_players()

        current_lower = current.lower()
        suggestions = []

        for player in players:
            display = player.get("display_name") or player.get("name", "")
            if not display:
                continue
            if current_lower and current_lower not in display.lower():
                continue

            suggestions.append(
                app_commands.Choice(
                    name=display[:100],
                    value=display
                )
            )
            if len(suggestions) >= 25:
                break

        return suggestions

    @staticmethod
    async def player_on_team_choices(
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """
        Get players on the user's team.
        Used by /sign, /trade, etc. in transactions.
        """
        cache = TeamPlayerCache()
        supabase = get_supabase()

        # Get user's team
        team = cache.get_team_by_owner(interaction.user.id) or await queries.get_team_by_owner(supabase, interaction.user.id)
        if not team:
            return []

        # Get players on that team
        player_names = cache.get_team_player_names(team["id"])

        # Filter to matching names
        current_lower = current.lower()
        filtered = [
            name for name in player_names
            if current_lower in name.lower()
        ]

        return [
            app_commands.Choice(name=name, value=name)
            for name in filtered[:25]
        ]

    # ==================== SEASONS ====================

    @staticmethod
    async def season_name_choices(current: str) -> List[app_commands.Choice[str]]:
        """Season choices, newest first (🟢 = active)."""
        try:
            seasons = await SeasonCache.ensure_ready()
        except Exception as exc:
            logger.error("Failed to load seasons for autocomplete: %s", exc, exc_info=True)
            seasons = []

        current_lower = current.lower()
        choices: List[app_commands.Choice[str]] = []
        for season in seasons:
            name = season.get("name") or f"Season {season.get('number')}"
            phase = (season.get("phase") or "").replace("_", " ")
            blob = f"{name} s{season.get('number')} {phase}".lower()
            if current_lower and current_lower not in blob:
                continue
            label = f"{'🟢' if season.get('active') else '⚪'} {name} — {phase.title() or 'Season'}"
            choices.append(app_commands.Choice(name=label[:100], value=name))
            if len(choices) >= 25:
                break
        return choices

    # ==================== LEAGUES ====================

    @staticmethod
    async def league_choices(current: str) -> List[app_commands.Choice[str]]:
        """Division choices in tier order."""
        await LeagueCache.ensure_ready()
        league_entries = get_league_entries()
        if not league_entries:
            return []

        current_lower = current.lower()
        filtered = [
            entry for entry in league_entries
            if not current_lower
            or current_lower in entry.get("code", "").lower()
            or current_lower in entry.get("name", "").lower()
        ]

        choices: List[app_commands.Choice[str]] = []
        for entry in filtered:
            code = (entry.get("code") or "").strip()
            if not code or code.lower() == "all":
                continue

            display = f"{entry.get('name', 'Unknown')} ({code.upper()})"[:100]
            choices.append(app_commands.Choice(name=display, value=code))
            if len(choices) >= 25:
                break

        return choices
