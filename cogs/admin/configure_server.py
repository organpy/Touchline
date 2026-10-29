"""``/configure-server``: opens the dashboard at its Settings section.

Also primes the guild configuration cache when the bot starts.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.config_utility import ConfigUtility
from utils.permissions import admin_only

logger = logging.getLogger(__name__)


class ServerConfigCog(commands.Cog):
    """Shortcut into the dashboard's settings."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="configure-server",
        description="[ADMIN] Admin role, channels and league name (opens the dashboard)",
    )
    @admin_only()
    async def configure_server(self, interaction: discord.Interaction) -> None:
        from cogs.dashboard.dashboard import open_dashboard
        from cogs.dashboard.settings import SettingsPage

        await open_dashboard(self.bot, interaction, SettingsPage)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerConfigCog(bot))
    try:
        await ConfigUtility.load_all_configs(bot)
    except Exception as exc:  # pragma: no cover - network path
        logger.error("Failed to prime configuration cache: %s", exc)
