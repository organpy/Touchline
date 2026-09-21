"""
Season summary cog: /season-summary command
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
from utils.embed_builder import EmbedBuilder
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


class SeasonSummaryCog(StatsCogBase):
    """Season summary command: /season-summary"""

    @app_commands.command(name="season-summary", description="[STATS] Show season overview and statistics")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def season_summary(
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

        # Fetch basic season stats
        try:
            standings_data = await queries.get_standings(self.supabase, season_row['id'])
            supabase = get_supabase()
            # Fetch all matches to get count
            response = await execute_async(supabase.table('matches').select(
                'id',
            ).eq('season_id', season_row['id']).eq('status', 'final'))
            total_matches = len(response.data) if response.data else 0

            # Get top scorer
            leaderboard = await queries.get_leaderboard(self.supabase, 'points', season_row['id'], 1)
            top_scorer = leaderboard[0] if leaderboard else None
        except Exception as exc:
            logger.error(f"Failed to fetch season summary for season {season_row['id']}: {exc}")
            standings_data = None
            total_matches = 0
            top_scorer = None

        if not standings_data:
            await interaction.followup.send(
                f"ℹ️ No summary data found for {league_season_display}."
            )
            return

        # Build embed
        season_league_display = self._format_league_display(league_meta)
        league_code = (league_meta or {}).get('code', '').upper() if league_meta else ''
        
        builder = EmbedBuilder(
            title=f"🎯 Season Summary — {season_row.get('name')}",
            color=discord.Color.gold(),
        )
        builder.add_league_field("League", season_league_display, league_code)
        builder.add_field("Season", season_row.get('name', 'Unknown'), inline=True)
        builder.add_field("Total Matches Played", str(total_matches), inline=True)

        # Quick stats
        total_teams = len(standings_data)
        builder.add_field("Total Teams", str(total_teams), inline=True)

        # Top teams
        if standings_data and len(standings_data) >= 3:
            top_3 = standings_data[:3]
            top_teams_str = "\n".join(
                f"{idx+1}. {team.get('team_name', 'Unknown')} ({team.get('wins', 0)}-{team.get('losses', 0)}-{team.get('draws', 0)})"
                for idx, team in enumerate(top_3)
            )
            builder.add_field("Top 3 Teams (by Wins)", top_teams_str, inline=False)

        # Top scorer
        if top_scorer:
            builder.add_field(
                "Top Scorer",
                f"{top_scorer.get('player_name', 'Unknown')}: {top_scorer.get('value', 0)} points",
                inline=False,
            )

        builder.add_timestamp()
        builder.set_footer_text("Season overview from recorded data")

        embed = builder.build()
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(SeasonSummaryCog(bot))
