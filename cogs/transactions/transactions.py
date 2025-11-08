"""Discord cog scaffolding for transaction management."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional

from db import get_supabase
from db.transactions_repository import TransactionsRepository
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class TransactionsCog(commands.GroupCog, name="transactions"):
    """Slash-command entry point for roster transactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)
        self.config = get_config_manager()

    # ------------------------------------------------------------------
    # Signings
    # ------------------------------------------------------------------
    @app_commands.command(name="sign", description="Begin a signing offer for a player")
    @app_commands.describe(player="Discord member to sign", minutes_valid="Minutes before the offer expires")
    async def sign(self, interaction: discord.Interaction, player: discord.Member, minutes_valid: Optional[int] = 60):
        """Kick off a signing flow (placeholder until full flow is implemented)."""
        await interaction.response.defer(ephemeral=True)

        if minutes_valid and minutes_valid <= 0:
            await interaction.followup.send("❌ Expiry minutes must be positive.", ephemeral=True)
            return

        # TODO: Load team ownership from config, create pending signing entry, send confirmation view to player.
        await interaction.followup.send(
            "📝 Signing workflow is under construction. Your request has been noted.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Releases
    # ------------------------------------------------------------------
    @app_commands.command(name="release", description="Release a player from your roster")
    async def release(self, interaction: discord.Interaction, player: discord.Member):
        """Placeholder release command."""
        await interaction.response.defer(ephemeral=True)
        # TODO: Validate team ownership, mark roster row as ended, insert transaction record, refresh cache.
        await interaction.followup.send(
            "🗂️ Release workflow is not ready yet. Track progress in docs/transactions-todo.md.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @app_commands.command(name="config", description="View transaction configuration for this guild")
    async def view_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_config = await self.config.get_guild_config(interaction.guild_id)
        config_dict = guild_config.to_dict()
        transactions_cfg = config_dict.get('transactions', {}) if config_dict else {}

        embed = discord.Embed(title="⚙️ Transactions Configuration", color=discord.Color.blurple())
        if not transactions_cfg:
            embed.description = "No transaction configuration stored for this guild yet."
        else:
            for key, value in transactions_cfg.items():
                embed.add_field(name=key, value=str(value), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TransactionsCog(bot))
    logger.info("✅ TransactionsCog loaded")
