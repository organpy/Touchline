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
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)


class LeaderboardCog(StatsCogBase):
    """Leaderboard command: /leaderboard"""

    @app_commands.command(name="leaderboard", description="[STATS] Show top players for a stat in a season")
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

        # Build embed with EmbedBuilder for consistency and styling
        season_league_display = self._format_league_display(league_meta)
        league_code = (league_meta or {}).get('league_type', '').upper() or ''
        stat_display = stat.title().replace("_", " ")
        
        # Create leaderboard embed using EmbedBuilder factory
        builder = EmbedBuilder.leaderboard(
            stat_name=stat_display,
            season_name=season_row.get('name', 'Unknown'),
            league_code=league_code,
            league_name=season_league_display,
        )
        
        # Add top entries count
        builder.add_field("Top Entries", str(min(limit, len(leaderboard_data))), inline=True)

        # Add leaderboard rows with medal emojis
        medals = ["🥇", "🥈", "🥉"]
        for rank, entry in enumerate(leaderboard_data[:limit], 1):
            player_name = entry.get('player_name', 'Unknown')
            value = entry.get('value', 0)
            team_code = entry.get('team_code', '')

            if isinstance(value, float):
                value_str = f"{value:.2f}"
            else:
                value_str = str(value)

            medal = medals[rank - 1] if rank <= 3 else f"#{rank}"
            team_display = f" ({team_code.upper()})" if team_code else ""

            builder.add_stat_field(
                f"**{player_name}**{team_display}",
                value_str,
                rank=rank,
            )

        # Get first place player's team for logo and add team image if available
        first_place_team = leaderboard_data[0].get('team_code', '') if leaderboard_data else ''
        if first_place_team:
            builder.add_team_image(first_place_team, as_thumbnail=True)

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, files=files)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(LeaderboardCog(bot))
