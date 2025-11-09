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
        self._division_by_league_code: Dict[str, Dict[str, Any]] = {}

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
        if not league_meta:
            return "Unknown"
        code = (league_meta.get('code') or '').upper()
        name = league_meta.get('name')
        if name and code and name.upper() != code:
            return f"{name} ({code})"
        return name or code or "Unknown"

    async def _validate_player_in_season(self, player: Dict[str, Any], season: Dict[str, Any]) -> bool:
        """Check if player actually has a roster entry in this season (not just cache data)."""
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
        """Check if team actually plays in this season (season's division matches team's division)."""
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

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        membership_hint = self._select_player_membership(player, None)
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=(membership_hint or {}).get('season_id'),
            default_league_code=(membership_hint or {}).get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate player plays in this season
        player_in_season = await self._validate_player_in_season(player, season_row)
        if not player_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Player **{player.get('display_name', player_name)}** was not on any roster in {league_display} "
                f"**{season_row.get('name')}**."
            )
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

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_display = (membership or {}).get('team_name') or averages.get('team_name', 'Free Agent')

        embed = discord.Embed(
            title=f"Season Averages — {player.get('display_name', player_name)}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Player League", value=player_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Team", value=team_display, inline=True)
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

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        membership_hint = self._select_player_membership(player, None)
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=(membership_hint or {}).get('season_id'),
            default_league_code=(membership_hint or {}).get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate player plays in this season
        player_in_season = await self._validate_player_in_season(player, season_row)
        if not player_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Player **{player.get('display_name', player_name)}** was not on any roster in {league_display} "
                f"**{season_row.get('name')}**."
            )
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

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_display = (membership or {}).get('team_name') or totals.get('team_name', 'Free Agent')

        embed = discord.Embed(
            title=f"Season Totals — {player.get('display_name', player_name)}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Player League", value=player_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Team", value=team_display, inline=True)
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

        cache = TeamPlayerCache()
        team_data = cache.get_team_by_code(team.strip().upper())
        if not team_data:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=team_data.get('active_season_id'),
            default_league_code=team_data.get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate team plays in this season
        team_in_season = await self._validate_team_in_season(team_data, season_row)
        if not team_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Team **{team_data.get('name', team)}** does not play in {league_display} **{season_row.get('name')}**."
            )
            return

        if not league_meta:
            league_meta = await self._fetch_league_metadata(team_data.get('league_type'))

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

        cache = TeamPlayerCache()
        team_data = cache.get_team_by_code(team.strip().upper())
        if not team_data:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=team_data.get('active_season_id'),
            default_league_code=team_data.get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate team plays in this season
        team_in_season = await self._validate_team_in_season(team_data, season_row)
        if not team_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Team **{team_data.get('name', team)}** does not play in {league_display} **{season_row.get('name')}**."
            )
            return

        if not league_meta:
            league_meta = await self._fetch_league_metadata(team_data.get('league_type'))

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

        player = await self._resolve_player(player_name)
        if not player:
            await interaction.followup.send(f"❌ Player `{player_name}` was not found.")
            return

        membership_hint = self._select_player_membership(player, None)
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=(membership_hint or {}).get('season_id'),
            default_league_code=(membership_hint or {}).get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate player plays in this season
        player_in_season = await self._validate_player_in_season(player, season_row)
        if not player_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Player **{player.get('display_name', player_name)}** was not on any roster in {league_display} "
                f"**{season_row.get('name')}**."
            )
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

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_display = (membership or {}).get('team_name')

        embed = discord.Embed(
            title=f"Match History — {player.get('display_name', player_name)}",
            color=discord.Color.teal(),
        )
        embed.add_field(name="Player League", value=player_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        if team_display:
            embed.add_field(name="Team", value=team_display, inline=True)
        embed.timestamp = datetime.utcnow()

        for match in history[:limit]:
            match_date_raw = match.get('match_date')
            date_str = 'Unknown'
            match_dt = None
            if isinstance(match_date_raw, datetime):
                match_dt = match_date_raw
            elif isinstance(match_date_raw, str) and match_date_raw:
                normalized = match_date_raw.replace('Z', '+00:00')
                try:
                    match_dt = datetime.fromisoformat(normalized)
                except ValueError:
                    match_dt = None
                if not match_dt:
                    date_str = match_date_raw.split('T')[0]
            if match_dt:
                date_str = match_dt.strftime('%b %d')

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
                f"`P:{points}  A:{assists}  G:{goals}  S:{saves}  B:{blocks}`\n"
                f"Power Plays: {pp_plays}/{pp_used}"
            )
            embed.add_field(name="\u200b", value=line, inline=False)

        embed.set_footer(text="Most recent matches shown first")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="compare", description="Compare two players' stats for a season")
    @app_commands.describe(
        player_a="First player display name",
        player_b="Second player display name",
        league="League code or name (if both players in same league, defaults to their active league)",
        season="Season name (e.g., S10). If both in same league, defaults to active season",
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

        player_a_data = await self._resolve_player(player_a)
        if not player_a_data:
            await interaction.followup.send(f"❌ Player `{player_a}` was not found.")
            return

        player_b_data = await self._resolve_player(player_b)
        if not player_b_data:
            await interaction.followup.send(f"❌ Player `{player_b}` was not found.")
            return

        # If league/season specified, use that for both players
        if league or season:
            context = await self._find_season(league, season)
            season_row, league_meta = context
            if not season_row:
                await interaction.followup.send("❌ Could not locate a season matching your request.")
                return

            # Validate both players play in this specific season
            player_a_in_season = await self._validate_player_in_season(player_a_data, season_row)
            player_b_in_season = await self._validate_player_in_season(player_b_data, season_row)
            
            league_display = self._format_league_display(league_meta)
            season_display = season_row.get('name', 'Unknown')
            
            if not player_a_in_season:
                await interaction.followup.send(
                    f"❌ Player **{player_a_data.get('display_name', player_a)}** was not on any roster in {league_display} **{season_display}**."
                )
                return
            
            if not player_b_in_season:
                await interaction.followup.send(
                    f"❌ Player **{player_b_data.get('display_name', player_b)}** was not on any roster in {league_display} **{season_display}**."
                )
                return
        else:
            # No league/season specified: Compare players in their OWN active leagues/seasons independently
            membership_a = self._select_player_membership(player_a_data, None)
            membership_b = self._select_player_membership(player_b_data, None)
            
            if not membership_a:
                await interaction.followup.send(
                    f"ℹ️ Player **{player_a_data.get('display_name', player_a)}** has no active roster membership."
                )
                return
            
            if not membership_b:
                await interaction.followup.send(
                    f"ℹ️ Player **{player_b_data.get('display_name', player_b)}** has no active roster membership."
                )
                return
            
            season_a_id = membership_a.get('season_id')
            season_b_id = membership_b.get('season_id')
            
            # Fetch season data for each player
            season_row_a = await self._get_season_by_id(season_a_id)
            season_row_b = await self._get_season_by_id(season_b_id)
            
            if not season_row_a or not season_row_b:
                await interaction.followup.send("❌ Could not fetch season data for one or both players.")
                return
            
            # Get league info for each player
            league_meta_a = await self._resolve_league_for_membership(membership_a)
            league_meta_b = await self._resolve_league_for_membership(membership_b)
            
            # Build comparison string for display
            season_row = season_row_a  # Use player A's season as primary
            league_meta = league_meta_a
            
            league_a_display = self._format_league_display(league_meta_a)
            league_b_display = self._format_league_display(league_meta_b)
            season_a_display = season_row_a.get('name', 'Unknown')
            season_b_display = season_row_b.get('name', 'Unknown')
            
            # If in same season, compare there; otherwise compare in their respective seasons
            if season_a_id == season_b_id:
                # Same season: straightforward comparison
                season_row = season_row_a
            else:
                # Different seasons: still compare in player A's season but note the difference
                season_row = season_row_a

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
