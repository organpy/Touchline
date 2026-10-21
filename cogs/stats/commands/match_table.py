"""
Match Table cog: /match-table command
Shows all games (played and scheduled) in a grid image format.
"""

from __future__ import annotations

import asyncio
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
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


class MatchTableCog(StatsCogBase):
    """Match Table command: /match-table"""

    @app_commands.command(name="match-table", description="[STATS] Show all games (played and scheduled) in a grid")
    @app_commands.describe(
        league="Division code or name (defaults to your team's division)",
        season="Season name or number. Defaults to the active season",
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

        # Resolve season and division (default: your team's division, else the top division)
        season_row, league_meta = await self._resolve_season_context(league_input=league, season_input=season)
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return
        if not league_meta:
            own_team = TeamPlayerCache.get_team_by_owner(interaction.user.id) or {}
            await LeagueCache.load_season_teams(season_row['id'])
            league_meta = LeagueCache.team_division(own_team.get('id'), season_row['id']) or LeagueCache.top_division()
        if not league_meta:
            await interaction.followup.send("ℹ️ No divisions have been set up yet.")
            return

        league_season_display = self._format_league_season_display(league_meta, season_row)
        league_code = (league_meta.get('code') or '').upper()
        league_display = self._format_league_display(league_meta)

        try:
            matches = [
                m for m in await queries.get_all_matches_for_season(self.supabase, season_row['id'])
                if m.get('stage', 'regular') == 'regular' and m.get('division_id') == league_meta['id']
            ]
        except Exception as exc:
            logger.error(f"Failed to fetch matches for season {season_row['id']}: {exc}")
            matches = []

        await LeagueCache.load_season_teams(season_row['id'])
        teams = sorted(
            (TeamPlayerCache.get_team(team_id) for team_id in LeagueCache.team_ids_in_division(league_meta['id'], season_row['id'])),
            key=lambda t: ((t or {}).get('name') or '').lower(),
        )
        teams = [t for t in teams if t]
        if not teams:
            await interaction.followup.send(f"ℹ️ No teams found for {league_season_display}.")
            return

        games_per_matchup = LeagueCache.settings(league_meta['id']).games_per_matchup

        # Generate the match table image (CPU-bound rendering runs in a worker thread)
        match_table_image = await asyncio.to_thread(
            generate_match_table_image,
            matches,
            teams,
            season_row.get('name', 'Unknown'),
            league_display or league_code or 'League',
            league_code,
            games_per_matchup,
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
