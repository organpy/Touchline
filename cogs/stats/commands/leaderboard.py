"""
Leaderboard cog: /leaderboard command
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
    _metric_autocomplete,
    _league_autocomplete,
    _season_autocomplete,
)

logger = logging.getLogger(__name__)


class LeaderboardCog(StatsCogBase):
    """Leaderboard command: /leaderboard"""

    @app_commands.command(name="leaderboard", description="Show top players for a stat in a season")
    @app_commands.describe(
        stat="Stat to rank by (points, assists, blocks, etc.)",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
        limit="Number of players to show (default 10)",
    )
    @app_commands.autocomplete(
        stat=_metric_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        stat: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
        limit: app_commands.Range[int, 3, 50] = 10,
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

        # Validate stat
        valid_stats = [
            'points', 'assists', 'blocks', 'goals', 'saves', 'own_goals',
            'powerup_plays', 'ppg', 'apg', 'bpg', 'gpg', 'spg', 'ogpg',
        ]
        stat_normalized = stat.lower().replace(' ', '_')
        if stat_normalized not in valid_stats:
            await interaction.followup.send(
                f"❌ Stat `{stat}` is not valid. Try: {', '.join(valid_stats[:5])}..."
            )
            return

        # Fetch leaderboard
        try:
            leaderboard_data = await queries.get_leaderboard(
                self.supabase,
                stat_normalized,
                season_row['id'],
                limit,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch leaderboard for stat {stat_normalized} season {season_row['id']}: {exc}")
            leaderboard_data = None

        if not leaderboard_data:
            await interaction.followup.send(
                f"ℹ️ No leaderboard data found for **{stat}** in season `{season_row.get('name')}`."
            )
            return

        # Build embed
        season_league_display = self._format_league_display(league_meta)
        stat_display = stat.title()
        embed = discord.Embed(
            title=f"Leaderboard — {stat_display}",
            color=discord.Color.red(),
        )
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)

        # Add leaderboard rows
        for rank, entry in enumerate(leaderboard_data[:limit], 1):
            player_name = entry.get('player_name', 'Unknown')
            value = entry.get('value', 0)

            if isinstance(value, float):
                value_str = f"{value:.2f}"
            else:
                value_str = str(value)

            embed.add_field(
                name=f"#{rank}",
                value=f"{player_name}: **{value_str}**",
                inline=False,
            )

        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="From recorded matches")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(LeaderboardCog(bot))
