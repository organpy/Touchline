"""
Player stats cogs: /average, /total-stats, /player-history
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from cogs.stats.base import StatsCogBase
from cogs.stats.autocomplete import (
    _player_name_autocomplete,
    _team_autocomplete,
    _league_autocomplete,
    _season_autocomplete,
)
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)


class PlayerStatsCog(StatsCogBase):
    """Player stats commands: /average, /total-stats, /player-history"""

    @app_commands.command(name="average", description="[STATS] Show per-game averages for a player in a season")
    @app_commands.describe(
        player_name="Player display name",
        team="Team code or name (optional filter for player autocomplete)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        player_name=_player_name_autocomplete,
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
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
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`."
            )
            return

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_code = (membership or {}).get('team_code') or ''
        team_display = (membership or {}).get('team_name') or averages.get('team_name', 'Free Agent')
        league_code = (league_meta or {}).get('league_type', '').upper() or ''

        # Build embed with EmbedBuilder
        builder = EmbedBuilder(
            title=f"📊 Season Averages — {player.get('display_name', player_name)}",
            color=discord.Color.blue(),
        )
        builder.add_team_field("Team", team_display, team_code)
        builder.add_field("Season", season_row.get('name', 'Unknown'), inline=True)
        builder.add_league_field("League", season_league_display, league_code)
        builder.add_field("Games Played", str(averages.get('games_played', 0)), inline=True)

        production_lines = [
            f"**PPG**: {averages.get('ppg', 0)}",
            f"**APG**: {averages.get('apg', 0)}",
            f"**BPG**: {averages.get('bpg', 0)}",
            f"**GPG**: {averages.get('gpg', 0)}",
            f"**SPG**: {averages.get('spg', 0)}",
            f"**OGPG**: {averages.get('ogpg', 0)}",
            f"**PP Efficiency**: {averages.get('pp_efficiency', 0)}%",
        ]
        builder.add_field("Per Game Stats", "\n".join(production_lines), inline=False)
        builder.add_timestamp()
        builder.set_footer_text("Stats sourced from recorded match data")
        
        if team_code:
            builder.add_team_image(team_code, as_thumbnail=True)

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, files=files or None)

    @app_commands.command(name="total-stats", description="[STATS] Show season totals for a player")
    @app_commands.describe(
        player_name="Player display name",
        team="Team code or name (optional filter for player autocomplete)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        player_name=_player_name_autocomplete,
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
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
                f"ℹ️ No recorded stats for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`."
            )
            return

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_display = (membership or {}).get('team_name') or totals.get('team_name', 'Free Agent')
        team_code = (membership or {}).get('team_code') or ''
        league_code = (league_meta or {}).get('league_type', '').upper() or ''

        builder = EmbedBuilder(
            title=f"📈 Season Totals — {player.get('display_name', player_name)}",
            color=discord.Color.gold(),
        )
        builder.add_field("Player League", player_league_display, inline=True)
        builder.add_field("Season", season_row.get('name', 'Unknown'), inline=True)
        builder.add_league_field("Season League", season_league_display, league_code)
        builder.add_team_field("Team", team_display, team_code)
        builder.add_field("Games Played", str(totals.get('games_played', 0)), inline=True)

        totals_lines = [
            f"**Points**: {totals.get('total_points', 0)}",
            f"**Assists**: {totals.get('total_assists', 0)}",
            f"**Blocks**: {totals.get('total_blocks', 0)}",
            f"**Goals**: {totals.get('total_goals', 0)}",
            f"**Saves**: {totals.get('total_saves', 0)}",
            f"**Own Goals**: {totals.get('total_own_goals', 0)}",
            f"**Powerup Plays**: {totals.get('total_pp_plays', 0)} (used: {totals.get('total_pp_used', 0)})",
        ]
        builder.add_field("Totals", "\n".join(totals_lines), inline=False)
        builder.add_timestamp()
        builder.set_footer_text("Stats sourced from recorded match data")
        
        if team_code:
            builder.add_team_image(team_code, as_thumbnail=True)

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, files=files or None)

    @app_commands.command(name="player-history", description="[STATS] Show game-by-game stats for a player")
    @app_commands.describe(
        player_name="Player display name",
        team="Team code or name (optional filter)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        limit="Number of recent games to show (default 10)",
    )
    @app_commands.autocomplete(
        player_name=_player_name_autocomplete,
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
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
                limit,
            )
        except Exception as exc:
            logger.error("Failed to fetch player history for player %s season %s: %s", player['id'], season_row['id'], exc)
            history = []

        if not history:
            await interaction.followup.send(
                f"ℹ️ No recorded match history for **{player.get('display_name', player_name)}** in season `{season_row.get('name')}`."
            )
            return

        membership = self._select_player_membership(player, season_row.get('id'))
        membership_league_meta = await self._resolve_league_for_membership(membership)
        if not league_meta:
            league_meta = membership_league_meta or league_meta
        player_league_display = self._format_league_display(membership_league_meta)
        season_league_display = self._format_league_display(league_meta)
        team_display = (membership or {}).get('team_name')
        team_code = (membership or {}).get('team_code') or ''
        league_code = (league_meta or {}).get('league_type', '').upper() or ''

        builder = EmbedBuilder(
            title=f"📋 Match History — {player.get('display_name', player_name)}",
            color=discord.Color.teal(),
        )
        builder.add_field("Player League", player_league_display, inline=True)
        builder.add_field("Season", season_row.get('name', 'Unknown'), inline=True)
        builder.add_league_field("Season League", season_league_display, league_code)
        if team_display:
            builder.add_team_field("Team", team_display, team_code)
        builder.add_timestamp()

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
            builder.add_field("\u200b", line, inline=False)

        builder.set_footer_text("Most recent matches shown first")
        
        if team_code:
            builder.add_team_image(team_code, as_thumbnail=True)

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, files=files or None)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(PlayerStatsCog(bot))
