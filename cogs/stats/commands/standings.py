"""
Standings cog: /standings command
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from cogs.stats.base import StatsCogBase
from cogs.stats.autocomplete import (
    _league_autocomplete,
    _season_autocomplete,
)
from utils.standings_image_generator import generate_standings_image

logger = logging.getLogger(__name__)


class StandingsCog(StatsCogBase):
    """Standings command: /standings"""

    @app_commands.command(name="standings", description="[STATS] Show current season standings for a league")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def standings(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Resolve season
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=None,
            default_league_code=None,
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

            league_code = (league_meta or {}).get('code') or season_row.get('league_type')
        # Fetch standings
        try:
            standings_data = await queries.get_standings(
                self.supabase,
                season_row['id'],
                league_type=(league_code or '').upper() or None,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch standings for season {season_row['id']}: {exc}")
            standings_data = None

        if not standings_data:
            league_display = self._format_league_display(league_meta)
            await interaction.followup.send(
                f"ℹ️ No standings recorded for {league_display} — {season_row.get('name', 'Unknown')} yet."
            )
            return

        season_league_display = self._format_league_display(league_meta)
        league_code = (league_code or '').upper()

        # Generate standings image
        standings_image = generate_standings_image(
            standings_data,
            season_row.get('name', 'Unknown'),
            season_league_display,
            league_code=league_code,
        )

        if standings_image:
            standings_file = discord.File(standings_image, filename="standings.png")
            await interaction.followup.send(file=standings_file)
        else:
            # Fallback to text table if image generation fails
            header = f"Standings — {season_league_display} ({season_row.get('name', 'Unknown')})"
            table_lines = [header, "```", "Rk  Team              W  L  PF   PA   Diff"]
            table_lines.append("-" * 50)

            for rank, team in enumerate(standings_data, 1):
                emoji = team.get('emoji', '⚽')
                team_name = team.get('name', 'Unknown')[:12].ljust(12)
                wins = str(team.get('wins', 0)).rjust(2)
                losses = str(team.get('losses', 0)).rjust(2)
                pf = str(team.get('points_for', 0)).rjust(4)
                pa = str(team.get('points_against', 0)).rjust(4)
                diff = str(team.get('point_diff', 0)).rjust(5)

                line = f"{rank:<2} {emoji} {team_name}  {wins}  {losses}  {pf}  {pa}  {diff}"
                table_lines.append(line)

            table_lines.append("```")
            await interaction.followup.send("\n".join(table_lines))


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(StandingsCog(bot))
