"""Slash commands for season statistics summaries."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


async def _player_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Return player name suggestions from the TeamPlayerCache."""
    cache = TeamPlayerCache()
    namespace = getattr(interaction, 'namespace', None)
    team_input = getattr(namespace, 'team', None) if namespace else None

    if team_input:
        # Filter players by team
        team = cache.get_team_by_code(team_input.strip().upper())
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
    """Autocomplete for league names/codes (module-level coroutine required by discord)."""
    supabase = get_supabase()
    try:
        # Query distinct league_type from divisions since leagues table doesn't exist
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

    current_lower = current.lower()
    suggestions = []
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


async def _team_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    """Autocomplete for team names/codes (module-level coroutine required by discord)."""
    cache = TeamPlayerCache()
    teams = cache.get_all_teams()
    current_lower = current.lower()

    suggestions = []
    for team in teams:
        code = (team.get('code') or '').upper()
        name = team.get('name') or code
        label = f"{name} ({code})" if name and code and name.upper() != code else name or code
        if current_lower and current_lower not in label.lower() and current_lower not in code.lower():
            continue
        suggestions.append(app_commands.Choice(name=label[:100], value=code or name))
        if len(suggestions) >= 25:
            break
    return suggestions


class StatsCog(commands.Cog):
    """Discord slash commands for player and league statistics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()
        self._league_cache: Dict[str, Dict[str, Any]] = {}
        self._division_cache: Dict[str, Dict[str, Any]] = {}

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

    async def _resolve_league_for_team(self, team: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Look up the league metadata for a specific team via its division."""
        if not team:
            return None

        division_id = team.get('division_id')
        if not division_id:
            return None

        division = self._division_cache.get(division_id)
        if division is None:
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
            except Exception as exc:  # pragma: no cover - network failure path
                logger.error(
                    "Failed to resolve division %s for team %s: %s",
                    division_id,
                    team.get('id'),
                    exc,
                )
                division = None

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
        team="Team code or name (optional filter for player autocomplete)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player_name=_player_name_autocomplete, team=_team_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def average(
        self,
        interaction: discord.Interaction,
        player_name: str,
        team: Optional[str] = None,
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

        try:
            averages = await queries.get_player_averages(
                self.supabase,
                player['id'],
                season_row['id'],
            )
        except Exception as exc:
            logger.error(
                "Failed to fetch player averages for player %s season %s: %s",
                player['id'],
                season_row['id'],
                exc,
            )
            averages = None

        if not averages:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`.")
            return

        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Season Averages — {player.get('display_name', player_name)}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Team", value=averages.get('team_name', 'Free Agent'), inline=True)
        embed.add_field(name="Games Played", value=str(averages.get('games_played', 0)), inline=True)

        production_lines = [
            f"**PPG**: {averages.get('ppg', 0)}",
            f"**APG**: {averages.get('apg', 0)}",
            f"**BPG**: {averages.get('bpg', 0)}",
            f"**GPG**: {averages.get('gpg', 0)}",
            f"**SPG**: {averages.get('spg', 0)}",
            f"**OGPG**: {averages.get('ogpg', 0)}",
            f"**PP Efficiency**: {averages.get('pp_efficiency', 0)}%",
        ]
        embed.add_field(name="Per Game", value="\n".join(production_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats sourced from recorded match data")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="total-stats", description="Show total season numbers for a player")
    @app_commands.describe(
        player_name="Player display name",
        team="Team code or name (optional filter for player autocomplete)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player_name=_player_name_autocomplete, team=_team_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def total_stats(
        self,
        interaction: discord.Interaction,
        player_name: str,
        team: Optional[str] = None,
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

        try:
            totals = await queries.get_player_totals(
                self.supabase,
                player['id'],
                season_row['id'],
            )
        except Exception as exc:
            logger.error(
                "Failed to fetch player totals for player %s season %s: %s",
                player['id'],
                season_row['id'],
                exc,
            )
            totals = None

        if not totals:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`.")
            return

        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Season Totals — {player.get('display_name', player_name)}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Team", value=totals.get('team_name', 'Free Agent'), inline=True)
        embed.add_field(name="Games Played", value=str(totals.get('games_played', 0)), inline=True)

        totals_lines = [
            f"**Points**: {totals.get('total_points', 0)}",
            f"**Assists**: {totals.get('total_assists', 0)}",
            f"**Blocks**: {totals.get('total_blocks', 0)}",
            f"**Goals**: {totals.get('total_goals', 0)}",
            f"**Saves**: {totals.get('total_saves', 0)}",
            f"**Own Goals**: {totals.get('total_own_goals', 0)}",
            f"**Powerup Plays**: {totals.get('total_pp_plays', 0)} (used: {totals.get('total_pp_used', 0)})",
        ]
        embed.add_field(name="Totals", value="\n".join(totals_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats sourced from recorded match data")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="standings", description="Show league standings for a season")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        team="Team name/code filter (optional, shows teams containing this text)",
        limit="Number of teams to display (default 10)",
    )
    @app_commands.autocomplete(league=_league_autocomplete, season=_season_autocomplete, team=_team_autocomplete)
    async def standings(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
        team: Optional[str] = None,
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

        # Filter by team if provided
        if team:
            team_lower = team.lower().strip()
            standings_rows = [
                row for row in standings_rows
                if team_lower in (row.get('name') or '').lower() or team_lower in (row.get('code') or '').lower()
            ]
            if not standings_rows:
                await interaction.followup.send(f"ℹ️ No teams found matching '{team}' in the selected season.")
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

        await interaction.followup.send(embed=embed)


    @app_commands.command(name="team-stats", description="Show total season stats for a team")
    @app_commands.describe(
        team="Team code or name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(team=_team_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def team_stats(
        self,
        interaction: discord.Interaction,
        team: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        cache = TeamPlayerCache()
        team_data = cache.get_team_by_code(team.strip().upper())
        if not team_data:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        try:
            totals = await queries.get_team_totals(
                self.supabase,
                team_data['id'],
                season_row['id'],
            )
        except Exception as exc:
            logger.error(
                "Failed to fetch team totals for team %s season %s: %s",
                team_data['id'],
                season_row['id'],
                exc,
            )
            totals = None

        if not totals:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{team_data.get('name', team)}** in season `{season_row.get('name')}`.")
            return

        season_league_display = self._format_league_display(league_meta)
        team_league_meta = await self._resolve_league_for_team(team_data)
        team_league_display = self._format_league_display(team_league_meta)

        embed = discord.Embed(
            title=f"Team Season Totals — {team_data.get('name', team)}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Team League", value=team_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Players", value=str(totals.get('player_count', 0)), inline=True)
        embed.add_field(name="Games Played", value=str(totals.get('games_played', 0)), inline=True)

        totals_lines = [
            f"**Points**: {totals.get('total_points', 0)}",
            f"**Assists**: {totals.get('total_assists', 0)}",
            f"**Blocks**: {totals.get('total_blocks', 0)}",
            f"**Goals**: {totals.get('total_goals', 0)}",
            f"**Saves**: {totals.get('total_saves', 0)}",
            f"**Own Goals**: {totals.get('total_own_goals', 0)}",
            f"**Powerup Plays**: {totals.get('total_pp_plays', 0)} (used: {totals.get('total_pp_used', 0)})",
        ]
        embed.add_field(name="Season Totals", value="\n".join(totals_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Aggregated across all recorded matches")

        await interaction.followup.send(embed=embed)


    @app_commands.command(name="team-average", description="Show per-game averages for a team in a season")
    @app_commands.describe(
        team="Team code or name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(team=_team_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def team_average(
        self,
        interaction: discord.Interaction,
        team: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        cache = TeamPlayerCache()
        team_data = cache.get_team_by_code(team.strip().upper())
        if not team_data:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        try:
            averages = await queries.get_team_averages(self.supabase, team_data['id'], season_row['id'])
        except Exception as exc:
            logger.error("Failed to fetch team averages for team %s season %s: %s", team_data['id'], season_row['id'], exc)
            averages = None

        if not averages:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{team_data.get('name', team)}** in season `{season_row.get('name')}`.")
            return

        season_league_display = self._format_league_display(league_meta)
        team_league_meta = await self._resolve_league_for_team(team_data)
        team_league_display = self._format_league_display(team_league_meta)

        embed = discord.Embed(
            title=f"Team Per-Game Averages — {team_data.get('name', team)}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Team League", value=team_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Games Played", value=str(averages.get('games_played', 0)), inline=True)

        averages_lines = [
            f"**PPG**: {averages.get('ppg', 0)}",
            f"**APG**: {averages.get('apg', 0)}",
            f"**GPG**: {averages.get('gpg', 0)}",
            f"**SPG**: {averages.get('spg', 0)}",
            f"**BPG**: {averages.get('bpg', 0)}",
            f"**OGPG**: {averages.get('ogpg', 0)}",
            f"**PP Efficiency**: {averages.get('pp_efficiency', 0)}%",
        ]
        embed.add_field(name="Per-Game Averages", value="\n".join(averages_lines), inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Team averages aggregated across all player matches")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard", description="Show top players for a stat in a season")
    @app_commands.describe(
        metric="Stat to rank by (points, assists, goals, saves, blocks, pp_plays)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        limit="Number of players to show (default 10)",
    )
    @app_commands.autocomplete(league=_league_autocomplete, season=_season_autocomplete)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        metric: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
        limit: app_commands.Range[int, 3, 25] = 10,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Validate metric
        valid_metrics = ['points', 'assists', 'goals', 'saves', 'blocks', 'own_goals', 'pp_plays']
        if metric.lower() not in valid_metrics:
            await interaction.followup.send(
                f"❌ Invalid metric. Choose from: {', '.join(valid_metrics)}")
            return

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        try:
            leaders = await queries.get_leaderboard(
                self.supabase,
                metric.lower(),
                season_row['id'],
                limit
            )
        except Exception as exc:
            logger.error("Failed to fetch leaderboard for metric %s season %s: %s", metric, season_row['id'], exc)
            leaders = []

        if not leaders:
            await interaction.followup.send(f"ℹ️ No leaderboard data available for metric `{metric}` in season `{season_row.get('name')}`.")
            return

        league_display = self._format_league_display(league_meta)

        metric_display = metric.replace('_', ' ').title()
        embed = discord.Embed(
            title=f"Leaderboard — {metric_display}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.timestamp = datetime.utcnow()

        for leader in leaders[:limit]:
            rank = leader.get('rank', '?')
            player = leader.get('player_name', 'Unknown')
            team = leader.get('team_name', 'Free Agent')
            value = leader.get('value', 0)
            games = leader.get('games_played', 0)
            embed.add_field(
                name=f"#{rank} — {player}",
                value=f"{team} | {value} ({games} GP)",
                inline=False
            )

        embed.set_footer(text="Rankings based on season totals")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="player-history", description="Show game-by-game stats for a player")
    @app_commands.describe(
        player_name="Player display name",
        team="Team code or name (optional filter)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        limit="Number of recent games to show (default 10)",
    )
    @app_commands.autocomplete(player_name=_player_name_autocomplete, team=_team_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def player_history(
        self,
        interaction: discord.Interaction,
        player_name: str,
        team: Optional[str] = None,
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

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        try:
            history = await queries.get_player_history(
                self.supabase,
                player['id'],
                season_row['id'],
                limit
            )
        except Exception as exc:
            logger.error("Failed to fetch player history for player %s season %s: %s", player['id'], season_row['id'], exc)
            history = []

        if not history:
            await interaction.followup.send(
                f"ℹ️ No recorded match history for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`.")
            return

        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Match History — {player.get('display_name', player_name)}",
            color=discord.Color.teal(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.timestamp = datetime.utcnow()

        for match in history[:limit]:
            match_date = match.get('match_date')
            date_str = match_date.strftime('%m/%d') if match_date else 'Unknown'
            opponent = match.get('opponent_name', 'Unknown')
            points = match.get('points', 0)
            assists = match.get('assists', 0)
            goals = match.get('goals', 0)
            saves = match.get('saves', 0)
            blocks = match.get('blocks', 0)
            pp_plays = match.get('pp_plays', 0)
            pp_used = match.get('pp_used', 0)

            line = (
                f"**{date_str}** vs {opponent}\n"
                f"{points}P | {assists}A | {goals}G | {saves}S | {blocks}B | PP {pp_plays}/{pp_used}"
            )
            embed.add_field(name="\u200b", value=line, inline=False)

        embed.set_footer(text="Most recent matches shown first")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="compare", description="Compare two players' stats for a season")
    @app_commands.describe(
        player_a="First player display name",
        player_b="Second player display name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player_a=_player_name_autocomplete, player_b=_player_name_autocomplete, league=_league_autocomplete, season=_season_autocomplete)
    async def compare(
        self,
        interaction: discord.Interaction,
        player_a: str,
        player_b: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        player_a_data = await self._resolve_player(player_a)
        if not player_a_data:
            await interaction.followup.send(f"❌ Player `{player_a}` was not found.")
            return

        player_b_data = await self._resolve_player(player_b)
        if not player_b_data:
            await interaction.followup.send(f"❌ Player `{player_b}` was not found.")
            return

        try:
            comparison = await queries.compare_players(
                self.supabase,
                player_a_data['id'],
                player_b_data['id'],
                season_row['id']
            )
        except Exception as exc:
            logger.error("Failed to compare players %s vs %s season %s: %s", player_a_data['id'], player_b_data['id'], season_row['id'], exc)
            comparison = []

        if not comparison:
            await interaction.followup.send("ℹ️ Could not fetch comparison data for the selected players.")
            return

        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"{player_a_data.get('display_name', player_a)} vs {player_b_data.get('display_name', player_b)}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.timestamp = datetime.utcnow()

        for row in comparison:
            metric = row.get('metric', 'Unknown')
            val_a = row.get('player_a_value', 0)
            val_b = row.get('player_b_value', 0)
            diff = row.get('diff', 0)
            winner = "🔴" if diff < 0 else "🟢" if diff > 0 else "⚪"
            embed.add_field(
                name=metric,
                value=f"{player_a_data.get('display_name', player_a)}: {val_a}\n{player_b_data.get('display_name', player_b)}: {val_b}\n{winner} Diff: {diff:+}",
                inline=False
            )

        embed.set_footer(text="Green indicates advantage for Player A, Red for Player B")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="season-summary", description="Show season overview and top performers")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(league=_league_autocomplete, season=_season_autocomplete)
    async def season_summary(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        context = await self._find_season(league, season)
        season_row, league_meta = context
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        try:
            summary = await queries.get_season_summary(self.supabase, season_row['id'])
        except Exception as exc:
            logger.error("Failed to fetch season summary for season %s: %s", season_row['id'], exc)
            summary = None

        if not summary:
            await interaction.followup.send("ℹ️ No summary data available for the selected season.")
            return

        league_display = self._format_league_display(league_meta)

        embed = discord.Embed(
            title=f"Season Overview — {season_row.get('name', 'Unknown Season')}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.timestamp = datetime.utcnow()

        embed.add_field(
            name="Season Statistics",
            value=(
                f"**Matches**: {summary.get('total_matches', 0)}\n"
                f"**Teams**: {summary.get('total_teams', 0)}\n"
                f"**Players**: {summary.get('total_players', 0)}\n"
                f"**Avg Points/Match**: {summary.get('avg_points_per_match', 0)}"
            ),
            inline=False
        )

        top_scorer = summary.get('top_scorer_name', 'N/A')
        top_scorer_points = summary.get('top_scorer_points', 0)
        embed.add_field(
            name="Top Scorer",
            value=f"**{top_scorer}** with {top_scorer_points} points",
            inline=False
        )

        top_team = summary.get('top_team_name', 'N/A')
        top_team_points = summary.get('top_team_points', 0)
        embed.add_field(
            name="Top Scoring Team",
            value=f"**{top_team}** with {top_team_points} total points",
            inline=False
        )

        embed.set_footer(text="Summary based on all recorded match data")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Load the stats cog into the bot."""
    await bot.add_cog(StatsCog(bot))
