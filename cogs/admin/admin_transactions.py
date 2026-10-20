"""``/admin-transactions``: opens the dashboard at its Transactions section."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class AdminTransactionsCog(commands.Cog):
    """Shortcut into the dashboard's transaction tools."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="admin-transactions",
        description="[ADMIN] Rosters, picks, trades and offers (opens the dashboard)",
    )
    async def admin_transactions(self, interaction: discord.Interaction) -> None:
        from cogs.dashboard.dashboard import open_dashboard
        from cogs.dashboard.transactions import TransactionsPage

        await open_dashboard(self.bot, interaction, TransactionsPage)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminTransactionsCog(bot))
    logger.info("✅ AdminTransactionsCog loaded")
