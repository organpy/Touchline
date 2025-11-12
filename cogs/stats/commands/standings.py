"""
Standings cog: /standings command
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
    _league_autocomplete,
    _season_autocomplete,
)

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

        # Fetch standings
        try:
            standings_data = await queries.get_standings(self.supabase, season_row['id'])
        except Exception as exc:
            logger.error(f"Failed to fetch standings for season {season_row['id']}: {exc}")
            standings_data = None

        if not standings_data:
            await interaction.followup.send(
                f"ℹ️ No standings data found for season `{season_row.get('name')}`."
            )
            return

        # Build embed
        season_league_display = self._format_league_display(league_meta)
        embed = discord.Embed(
            title=f"Standings — {season_row.get('name')}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="League", value=season_league_display, inline=True)

        # Build standings table
        table_lines = ["```\nRk  Team              W  L  D  PF   PA   Diff"]
        table_lines.append("-" * 55)

        for rank, team in enumerate(standings_data, 1):
            team_name = team.get('team_name', 'Unknown')[:15].ljust(15)
            wins = str(team.get('wins', 0)).rjust(2)
            losses = str(team.get('losses', 0)).rjust(2)
            draws = str(team.get('draws', 0)).rjust(2)
            pf = str(team.get('points_for', 0)).rjust(4)
            pa = str(team.get('points_against', 0)).rjust(4)
            diff = str(team.get('points_diff', 0)).rjust(5)

            line = f"{rank:<2} {team_name}  {wins}  {losses}  {draws}  {pf}  {pa}  {diff}"
            table_lines.append(line)

        table_lines.append("```")
        table_str = "\n".join(table_lines)

        embed.add_field(name="Standings", value=table_str, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="W = Wins, L = Losses, D = Draws, PF = Points For, PA = Points Against, Diff = Difference")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(StandingsCog(bot))
