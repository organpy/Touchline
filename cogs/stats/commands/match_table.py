"""
Match Table cog: /match-table command
Shows all games (played and scheduled) in a grid image format.
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
from utils.match_table_image_generator import generate_match_table_image

logger = logging.getLogger(__name__)


class MatchTableCog(StatsCogBase):
    """Match Table command: /match-table"""

    @app_commands.command(name="match-table", description="[STATS] Show all games (played and scheduled) in a grid")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def match_table(
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

        league_season_display = self._format_league_season_display(league_meta, season_row)
        league_code = ((league_meta or {}).get('code') or season_row.get('league_type') or '').upper()
        league_display = self._format_league_display(league_meta)

        # Fetch all matches (played and scheduled)
        try:
            matches = await queries.get_all_matches_for_season(self.supabase, season_row['id'])
        except Exception as exc:
            logger.error(f"Failed to fetch matches for season {season_row['id']}: {exc}")
            matches = []

        # Fetch all teams in the division
        try:
            teams = await queries.get_all_teams(self.supabase, season_row.get('division_id'))
        except Exception as exc:
            logger.error(f"Failed to fetch teams for division: {exc}")
            teams = []

        if not teams:
            await interaction.followup.send(
                f"ℹ️ No teams found for {league_season_display}."
            )
            return

        # Generate the match table image
        match_table_image = generate_match_table_image(
            matches,
            teams,
            season_row.get('name', 'Unknown'),
            league_display or league_code or 'League',
            league_code,
        )

        if not match_table_image:
            await interaction.followup.send(
                f"❌ Failed to generate match table image for {league_season_display}."
            )
            return

        # Send the image
        file = discord.File(match_table_image, filename="match_table.png")
        await interaction.followup.send(file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchTableCog(bot))
