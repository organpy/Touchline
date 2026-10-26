"""Admin command to seed draft picks for a division in the active season."""

from __future__ import annotations

import logging
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from cogs.admin.picks import MAX_ROUNDS, build_pick_rows, division_teams
from db import get_supabase
from db.db_utils import execute_async
from db.transactions_repository import TransactionsRepository
from utils.league_cache import LeagueCache
from utils.permissions import admin_only

logger = logging.getLogger(__name__)


async def draft_division_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    """Divisions that run a draft."""
    await LeagueCache.ensure_ready()
    needle = current.lower()
    return [
        app_commands.Choice(name=f"{d.get('code')} · {d.get('name')}"[:100], value=d['code'])
        for d in LeagueCache.picks_divisions()
        if needle in f"{d.get('code')} {d.get('name')}".lower()
    ][:25]


class AdminSeedPicksCog(commands.GroupCog, name="admin_seed_picks"):
    """Admin helpers for seeding draft picks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)

    @app_commands.command(
        name="populate_picks",
        description="[ADMIN] Populate draft picks for a division in the active season"
    )
    @app_commands.describe(
        division="Division that runs a draft",
        num_rounds="Number of rounds (every team in the division gets one pick per round)",
    )
    @app_commands.autocomplete(division=draft_division_autocomplete)
    @admin_only()
    async def populate_picks(self, interaction: discord.Interaction, division: str, num_rounds: int):
        """Seed draft picks for one division of the active season."""
        await interaction.response.defer(ephemeral=True)

        if not 1 <= num_rounds <= MAX_ROUNDS:
            await interaction.followup.send(f"❌ Rounds must be between 1 and {MAX_ROUNDS}.", ephemeral=True)
            return

        await LeagueCache.ensure_ready()
        division_row = LeagueCache.find_division(division)
        if not division_row:
            await interaction.followup.send(f"❌ Division `{division}` not found.", ephemeral=True)
            return
        if not LeagueCache.settings(division_row['id']).picks_enabled:
            await interaction.followup.send(
                f"❌ **{division_row['name']}** doesn't run a draft. Enable picks in Dashboard → Divisions → Rules.",
                ephemeral=True,
            )
            return

        season = LeagueCache.active_season()
        if not season:
            await interaction.followup.send("⚠️ No active season.", ephemeral=True)
            return

        if await self.repo.list_draft_picks(season['id'], division_row['id']):
            await interaction.followup.send(
                f"⚠️ {division_row['name']} already has draft picks for {season['name']}.", ephemeral=True,
            )
            return

        teams = await division_teams(division_row['id'], season['id'])
        if len(teams) < 2:
            await interaction.followup.send(
                f"⚠️ {division_row['name']} needs at least 2 teams this season.", ephemeral=True,
            )
            return

        rows = build_pick_rows(teams, num_rounds, season_id=season['id'], division_id=division_row['id'])
        try:
            response = await execute_async(self.supabase.table('draft_picks').insert(rows))
            inserted = len(response.data) if response.data else 0
            await interaction.followup.send(
                f"✅ Seeded {inserted} draft picks for **{division_row['name']}**, {season['name']}:\n"
                f"  • {num_rounds} rounds × {len(teams)} teams",
                ephemeral=True,
            )
            logger.info("✅ Seeded %d draft picks for %s %s", inserted, division_row['code'], season['name'])
        except Exception as exc:
            logger.error("Failed to seed draft picks: %s", exc)
            await interaction.followup.send("❌ Failed to seed draft picks. Check logs for details.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminSeedPicksCog(bot))
    logger.info("✅ AdminSeedPicksCog loaded")
