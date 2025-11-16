"""
Team stats cogs: /team-stats, /team-average
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
    _team_autocomplete,
    _league_autocomplete,
    _season_autocomplete,
)
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)

logger = logging.getLogger(__name__)


class TeamStatsCog(StatsCogBase):
    """Team stats commands: /team-stats, /team-average"""

    @app_commands.command(name="team-stats", description="[STATS] Show roster stats for a team in a season")
    @app_commands.describe(
        team="Team code or name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def team_stats(
        self,
        interaction: discord.Interaction,
        team: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Resolve team and season
        team_row = await self._resolve_team(team)
        if not team_row:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=None,
            default_league_code=None,
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate team in season
        team_in_season = await self._validate_team_in_season(team_row, season_row)
        if not team_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Team **{team_row.get('name', team)}** was not in {league_display} **{season_row.get('name')}**."
            )
            return

        # Fetch team totals
        try:
            team_totals = await queries.get_team_totals(
                self.supabase,
                team_row['id'],
                season_row['id'],
            )
        except Exception as exc:
            logger.error(f"Failed to fetch team totals for team {team_row['id']} season {season_row['id']}: {exc}")
            team_totals = None

        if not team_totals:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{team_row.get('name', team)}** in season `{season_row.get('name')}`."
            )
            return

        # Build embed with styling
        season_league_display = self._format_league_display(league_meta)
        league_code = (league_meta or {}).get('league_type', '').upper() or ''
        team_code = team_row.get('code', '')
        
        # Get team logo
        files, thumbnail = build_team_logo_payload(team_code)
        
        embed = discord.Embed(
            title=f"🏀 Team Stats — {team_row.get('name', team)}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Team", value=f"**{team_row.get('name')}** (`{team_code}`)", inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="League", value=season_league_display, inline=True)

        totals_lines = [
            f"**Total Points**: {team_totals.get('total_points', 0)}",
            f"**Total Assists**: {team_totals.get('total_assists', 0)}",
            f"**Total Goals**: {team_totals.get('total_goals', 0)}",
            f"**Total Saves**: {team_totals.get('total_saves', 0)}",
            f"**Total Blocks**: {team_totals.get('total_blocks', 0)}",
            f"**Total Own Goals**: {team_totals.get('total_own_goals', 0)}",
            f"**Total PP Plays**: {team_totals.get('total_pp_plays', 0)} (used: {team_totals.get('total_pp_used', 0)})",
        ]
        embed.add_field(name="Season Totals", value="\n".join(totals_lines), inline=False)

        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats from all recorded team matches")
        
        # Add styling
        if thumbnail:
            target_thumbnail(embed, thumbnail)
        if team_code:
            apply_team_badge(embed, team_code)
        if league_code:
            apply_league_badge(embed, league_code)

        await interaction.followup.send(embed=embed, files=files or None)

    @app_commands.command(name="team-average", description="[STATS] Show average per-game stats for a team")
    @app_commands.describe(
        team="Team code or name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def team_average(
        self,
        interaction: discord.Interaction,
        team: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Resolve team and season
        team_row = await self._resolve_team(team)
        if not team_row:
            await interaction.followup.send(f"❌ Team `{team}` was not found.")
            return

        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=None,
            default_league_code=None,
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate team in season
        team_in_season = await self._validate_team_in_season(team_row, season_row)
        if not team_in_season:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"❌ Team **{team_row.get('name', team)}** was not in {league_display} **{season_row.get('name')}**."
            )
            return

        # Fetch team averages
        try:
            team_avg = await queries.get_team_averages(
                self.supabase,
                team_row['id'],
                season_row['id'],
            )
        except Exception as exc:
            logger.error(f"Failed to fetch team averages for team {team_row['id']} season {season_row['id']}: {exc}")
            team_avg = None

        if not team_avg:
            await interaction.followup.send(
                f"ℹ️ No recorded stats for **{team_row.get('name', team)}** in season `{season_row.get('name')}`."
            )
            return

        # Build embed with styling
        season_league_display = self._format_league_display(league_meta)
        league_code = (league_meta or {}).get('league_type', '').upper() or ''
        team_code = team_row.get('code', '')
        
        # Get team logo
        files, thumbnail = build_team_logo_payload(team_code)
        
        embed = discord.Embed(
            title=f"📊 Team Per-Game Averages — {team_row.get('name', team)}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Team", value=f"**{team_row.get('name')}** (`{team_code}`)", inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)
        embed.add_field(name="League", value=season_league_display, inline=True)
        embed.add_field(name="Games Played", value=str(team_avg.get('games_played', 0)), inline=False)

        avg_lines = [
            f"**Points Per Game**: {team_avg.get('ppg', 0)}",
            f"**Assists Per Game**: {team_avg.get('apg', 0)}",
            f"**Goals Per Game**: {team_avg.get('gpg', 0)}",
            f"**Saves Per Game**: {team_avg.get('spg', 0)}",
            f"**Blocks Per Game**: {team_avg.get('bpg', 0)}",
            f"**Own Goals Per Game**: {team_avg.get('ogpg', 0)}",
            f"**PP Efficiency**: {team_avg.get('pp_efficiency', 0)}%",
        ]
        embed.add_field(name="Per Game Averages", value="\n".join(avg_lines), inline=False)

        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Stats from all recorded team matches")
        
        # Add styling
        if thumbnail:
            target_thumbnail(embed, thumbnail)
        if team_code:
            apply_team_badge(embed, team_code)
        if league_code:
            apply_league_badge(embed, league_code)

        await interaction.followup.send(embed=embed, files=files or None)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(TeamStatsCog(bot))
