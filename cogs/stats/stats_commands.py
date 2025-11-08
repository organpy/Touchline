"""Slash commands for season statistics summaries."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from utils.stat_calculations import calculate_season_averages, calculate_season_totals
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


async def _player_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Return player name suggestions from the TeamPlayerCache."""
    cache = TeamPlayerCache()
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
    """Autocomplete for league names/codes (module-level coroutine required by discord)."""
    supabase = get_supabase()
    try:
        response = (
            supabase
            .table('leagues')
            .select('code, name')
            .order('name', desc=False)
            .limit(25)
            .execute()
        )
        leagues = response.data or []
    except Exception as exc:  # pragma: no cover - network failure path
        logger.error("Failed to fetch leagues for autocomplete: %s", exc)
        leagues = []

    current_lower = current.lower()
    suggestions = []
    for league in leagues:
        code = (league.get('code') or '').upper()
        name = league.get('name') or code
        label = f"{name} ({code})" if name and code and name.upper() != code else name or code
        if current_lower and current_lower not in label.lower():
            continue
        suggestions.append(app_commands.Choice(name=label[:100], value=code or name))
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
        query = supabase.table('seasons').select('name, league_type').order('created_at', desc=True).limit(50)
        if league_input:
            query = query.ilike('league_type', str(league_input))
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


class StatsCog(commands.Cog):
    """Discord slash commands for player and league statistics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()
        self._league_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Helpers
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

        # Try exact code match first
        try:
            response = (
                self.supabase
                .table('leagues')
                .select('*')
                .eq('code', normalized)
                .limit(1)
                .execute()
            )
            record = (response.data or [None])[0]
            if not record:
                response = (
                    self.supabase
                    .table('leagues')
                    .select('*')
                    .ilike('name', f"%{identifier}%")
                    .limit(1)
                    .execute()
                )
                record = (response.data or [None])[0]
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch league metadata for %s: %s", identifier, exc)
            record = None

        if record:
            code = (record.get('code') or identifier).upper()
            self._league_cache[code] = record
            return record

        # Cache a fallback entry so we do not keep querying
        fallback = {'code': normalized, 'name': identifier}
        self._league_cache[normalized] = fallback
        return fallback

    async def _find_season(
        self,
        league_input: Optional[str],
        season_input: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Locate a season row and its league metadata."""
        league_meta = await self._fetch_league_metadata(league_input)
        league_code = (league_meta or {}).get('code') if league_meta else None

        def _build_query(active_only: bool = False):
            query = self.supabase.table('seasons').select('*')
            if season_input:
                query = query.ilike('name', f"%{season_input}%")
            if league_code:
                query = query.ilike('league_type', league_code)
            elif league_input:
                query = query.ilike('league_type', league_input)
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
        return season_row, league_meta

    async def _resolve_league_for_season(self, season: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Derive league metadata from a season row."""
        league_code = season.get('league_type')
        if league_code:
            return await self._fetch_league_metadata(league_code)

        division_id = season.get('division_id')
        if not division_id:
            return None

        try:
            response = (
                self.supabase
                .table('divisions')
                .select('league_type, name')
                .eq('id', division_id)
                .limit(1)
                .execute()
            )
            division = (response.data or [None])[0]
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to resolve division %s for season %s: %s", division_id, season.get('id'), exc)
            division = None

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
    def _format_league_display(league_meta: Optional[Dict[str, Any]]) -> str:
        if not league_meta:
            return "Unknown"
        code = (league_meta.get('code') or '').upper()
        name = league_meta.get('name')
        if name and code and name.upper() != code:
            return f"{name} ({code})"
        return name or code or "Unknown"
    # ------------------------------------------------------------------
    # (autocomplete handlers moved to module-level functions)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    @app_commands.command(name="average", description="Show per-game averages for a player in a season")
    @app_commands.describe(
        player_name="Player display name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player_name=_player_name_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def average(
        self,
        interaction: discord.Interaction,
        player_name: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        stats = await queries.get_player_stats_for_season(self.supabase, player['id'], season_row['id'])
        if not stats:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`.")
            return

        averages = calculate_season_averages(stats)
        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Season Averages — {player.get('display_name', player_name)}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Games Played", value=str(averages['games_played']), inline=True)

        production_lines = [
            f"**PPG**: {averages['ppg']}",
            f"**APG**: {averages['apg']}",
            f"**BPG**: {averages['bpg']}",
            f"**GPG**: {averages['gpg']}",
            f"**SPG**: {averages['spg']}",
            f"**OGPG**: {averages['opg']}",
            f"**PP Efficiency**: {averages['pp_efficiency']}%",
        ]
        embed.add_field(name="Per Game", value="\n".join(production_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats sourced from recorded match data")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="total-stats", description="Show total season numbers for a player")
    @app_commands.describe(
        player_name="Player display name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player_name=_player_name_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def total_stats(
        self,
        interaction: discord.Interaction,
        player_name: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        stats = await queries.get_player_stats_for_season(self.supabase, player['id'], season_row['id'])
        if not stats:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`.")
            return

        totals = calculate_season_totals(stats)
        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Season Totals — {player.get('display_name', player_name)}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Games Played", value=str(totals['games_played']), inline=True)

        totals_lines = [
            f"**Points**: {totals['total_points']}",
            f"**Assists**: {totals['total_assists']}",
            f"**Blocks**: {totals['total_blocks']}",
            f"**Goals**: {totals['total_goals']}",
            f"**Saves**: {totals['total_saves']}",
            f"**Own Goals**: {totals['total_own_goals']}",
            f"**Powerup Plays**: {totals['total_pp_plays']} (used: {totals['total_pp_used']})",
        ]
        embed.add_field(name="Totals", value="\n".join(totals_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats sourced from recorded match data")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="standings", description="Show league standings for a season")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        limit="Number of teams to display (default 10)",
    )
    @app_commands.autocomplete(league=_league_autocomplete, season=_season_autocomplete)
    async def standings(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
        limit: app_commands.Range[int, 3, 25] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        league_code = (league_meta or {}).get('code') or season_row.get('league_type') or league or ''
        try:
            standings_rows = await queries.get_standings(self.supabase, season_row['id'], league_code)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Failed to fetch standings for season %s: %s", season_row.get('id'), exc)
            standings_rows = []

        if not standings_rows:
            await interaction.followup.send("ℹ️ No standings data available for the selected season.")
            return

        standings_sorted = sorted(
            standings_rows,
            key=lambda row: (
                row.get('competition_points', 0),
                row.get('point_diff', 0),
                row.get('wins', 0),
            ),
            reverse=True,
        )

        limited = standings_sorted[:limit]
        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Standings — {season_row.get('name', 'Unknown Season')}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Teams", value=str(len(standings_rows)), inline=True)
        embed.timestamp = datetime.utcnow()

        for index, row in enumerate(limited, start=1):
            team_name = row.get('name') or row.get('code', 'Unknown Team')
            record = f"{row.get('wins', 0)}-{row.get('losses', 0)}"
            comp_points = row.get('competition_points', 0)
            diff = row.get('point_diff', 0)
            points_for = row.get('points_for', 0)
            points_against = row.get('points_against', 0)
            standings_line = (
                f"Record: {record}\n"
                f"Points: {comp_points} | Diff: {diff:+}\n"
                f"PF: {points_for} | PA: {points_against}"
            )
            embed.add_field(name=f"{index}. {team_name}", value=standings_line, inline=False)

        embed.set_footer(text="Standings ordered by competition points and point differential")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
