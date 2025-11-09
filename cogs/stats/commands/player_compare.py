"""
Player comparison cog: /compare command
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
    _league_autocomplete,
    _season_autocomplete,
)

logger = logging.getLogger(__name__)


class PlayerCompareCog(StatsCogBase):
    """Player comparison command: /compare"""

    @app_commands.command(name="compare", description="Compare stats between two players in a season")
    @app_commands.describe(
        player_1="First player display name",
        player_2="Second player display name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        player_1=_player_name_autocomplete,
        player_2=_player_name_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def compare(
        self,
        interaction: discord.Interaction,
        player_1: str,
        player_2: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Resolve both players
        player_a = await self._resolve_player(player_1)
        if not player_a:
            await interaction.followup.send(f"❌ Player `{player_1}` was not found.")
            return

        player_b = await self._resolve_player(player_2)
        if not player_b:
            await interaction.followup.send(f"❌ Player `{player_2}` was not found.")
            return

        if player_a['id'] == player_b['id']:
            await interaction.followup.send("❌ Cannot compare a player with themselves.")
            return

        # Resolve season context
        membership_hint = self._select_player_membership(player_a, None)
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=(membership_hint or {}).get('season_id'),
            default_league_code=(membership_hint or {}).get('league_type'),
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        # Validate both players in season
        player_a_in_season = await self._validate_player_in_season(player_a, season_row)
        player_b_in_season = await self._validate_player_in_season(player_b, season_row)

        if not player_a_in_season or not player_b_in_season:
            league_display = self._format_league_display(league_meta)
            if not player_a_in_season:
                await interaction.followup.send(
                    f"❌ Player **{player_a.get('display_name', player_1)}** was not on any roster in {league_display} "
                    f"**{season_row.get('name')}**."
                )
            else:
                await interaction.followup.send(
                    f"❌ Player **{player_b.get('display_name', player_2)}** was not on any roster in {league_display} "
                    f"**{season_row.get('name')}**."
                )
            return

        # Fetch averages for both players
        try:
            avg_a = await queries.get_player_averages(self.supabase, player_a['id'], season_row['id'])
            avg_b = await queries.get_player_averages(self.supabase, player_b['id'], season_row['id'])
        except Exception as exc:
            logger.error(f"Failed to fetch averages for comparison: {exc}")
            avg_a = None
            avg_b = None

        if not avg_a or not avg_b:
            await interaction.followup.send(
                "ℹ️ One or both players have no recorded stats in this season."
            )
            return

        # Build comparison embed
        season_league_display = self._format_league_display(league_meta)
        embed = discord.Embed(
            title=f"Player Comparison — {season_row.get('name')}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Season League", value=season_league_display, inline=True)
        embed.add_field(name="Season", value=season_row.get('name', 'Unknown'), inline=True)

        # Stats rows
        stats_headers = ["Stat", player_a.get('display_name', player_1), player_b.get('display_name', player_2)]

        stats_to_show = [
            ("Games Played", "games_played"),
            ("PPG", "ppg"),
            ("APG", "apg"),
            ("BPG", "bpg"),
            ("GPG", "gpg"),
            ("SPG", "spg"),
            ("OGPG", "ogpg"),
            ("PP Efficiency", "pp_efficiency"),
        ]

        # Format into rows with win highlight
        rows = []
        for stat_name, stat_key in stats_to_show:
            val_a = avg_a.get(stat_key, 0)
            val_b = avg_b.get(stat_key, 0)

            # Format as string
            val_a_str = f"{val_a}" if isinstance(val_a, int) else f"{val_a:.2f}"
            val_b_str = f"{val_b}" if isinstance(val_b, int) else f"{val_b:.2f}"

            # Highlight winner
            if val_a > val_b:
                val_a_str = f"**{val_a_str}**"
            elif val_b > val_a:
                val_b_str = f"**{val_b_str}**"

            rows.append(f"{stat_name}: {val_a_str} vs {val_b_str}")

        embed.add_field(
            name="Per Game Stats",
            value="\n".join(rows),
            inline=False,
        )

        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Bold indicates higher stat. Stats from recorded matches.")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(PlayerCompareCog(bot))
