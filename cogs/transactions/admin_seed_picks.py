"""Admin command to seed draft picks for the active season."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional
from uuid import UUID

from db import get_supabase, queries
from db.db_utils import execute_async
from .pick_trading import get_nbl_division

logger = logging.getLogger(__name__)


class AdminSeedPicksCog(commands.GroupCog, name="admin_seed_picks"):
    """Admin helpers for seeding draft picks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()

    @app_commands.command(
        name="populate_picks",
        description="Populate draft picks for the active NBL season (admin only)"
    )
    @app_commands.describe(
        num_rounds="Number of rounds (e.g., 5)",
        picks_per_round="Number of picks per round (e.g., 8)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def populate_picks(
        self,
        interaction: discord.Interaction,
        num_rounds: int,
        picks_per_round: int
    ):
        """
        Seed draft picks for the active NBL season.

        Draft picks are NBL-only by design. Each team gets one pick per round.
        """
        await interaction.response.defer(ephemeral=True)

        if num_rounds < 1 or picks_per_round < 1:
            await interaction.followup.send(
                "❌ Rounds and picks per round must be positive integers.",
                ephemeral=True,
            )
            return

        nbl_division = await get_nbl_division(self.supabase)
        if not nbl_division:
            await interaction.followup.send("⚠️ NBL division is not configured.", ephemeral=True)
            return

        season = await queries.get_active_season(self.supabase, division_id=nbl_division['id'])
        if not season:
            await interaction.followup.send("⚠️ No active NBL season configured.", ephemeral=True)
            return

        teams = await queries.get_all_teams(self.supabase, season['division_id'])
        if not teams:
            await interaction.followup.send(
                "⚠️ No teams found for the active NBL season.",
                ephemeral=True,
            )
            return

        if len(teams) != picks_per_round:
            logger.warning(
                "Team count (%d) does not match picks_per_round (%d); "
                "some picks may be unassigned.",
                len(teams),
                picks_per_round
            )

        picks_to_insert = []
        overall_pick = 1

        for round_num in range(1, num_rounds + 1):
            for slot in range(picks_per_round):
                team_idx = slot % len(teams)
                team = teams[team_idx]

                picks_to_insert.append({
                    'season_id': season['id'],
                    'original_team_id': team['id'],
                    'current_owner_team_id': team['id'],
                    'round': round_num,
                    'overall': overall_pick,
                    'notes': None,
                    'metadata': {}
                })
                overall_pick += 1

        if not picks_to_insert:
            await interaction.followup.send("ℹ️ No picks to insert.", ephemeral=True)
            return

        try:
            response = await execute_async(self.supabase.table('draft_picks').insert(picks_to_insert))
            inserted = len(response.data) if response.data else 0
            await interaction.followup.send(
                f"✅ Seeded {inserted} draft picks for season {season['name']}:\n"
                f"  • {num_rounds} rounds × {picks_per_round} picks per round",
                ephemeral=True,
            )
            logger.info(
                "✅ Seeded %d draft picks for season %s (rounds=%d, per_round=%d)",
                inserted,
                season['name'],
                num_rounds,
                picks_per_round
            )
        except Exception as exc:
            logger.error("Failed to seed draft picks: %s", exc)
            await interaction.followup.send(
                "❌ Failed to seed draft picks. Check logs for details.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminSeedPicksCog(bot))
    logger.info("✅ AdminSeedPicksCog loaded")
