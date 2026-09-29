"""
Cog Management
Allows the bot owner to load/unload/reload cogs.
"""

import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

class CogManagementCog(commands.Cog):
    """Admin commands for managing bot cogs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="admin-cogs", description="[ADMIN] Manage bot extensions (cogs) - bot owner only")
    @app_commands.describe(action="Action to perform", extension="Extension name (e.g., cogs.stats.commands.leaderboard)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Load", value="load"),
        app_commands.Choice(name="Unload", value="unload"),
        app_commands.Choice(name="Reload", value="reload"),
        app_commands.Choice(name="List", value="list")
    ])
    async def manage_cogs(self, interaction: discord.Interaction, action: str, extension: Optional[str] = None):
        """Manage bot extensions."""
        # Extensions are global to the bot, so a server admin must not control them for every server.
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ Only the bot owner can manage extensions.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if action == "list":
            extensions = list(self.bot.extensions.keys())
            embed = discord.Embed(title="🧩 Loaded Extensions", description="\n".join(extensions), color=discord.Color.blue())
            await interaction.followup.send(embed=embed)
            return

        if not extension:
            await interaction.followup.send("❌ You must specify an extension name.", ephemeral=True)
            return

        try:
            if action == "load":
                await self.bot.load_extension(extension)
                await interaction.followup.send(f"✅ Loaded `{extension}`")
            elif action == "unload":
                await self.bot.unload_extension(extension)
                await interaction.followup.send(f"✅ Unloaded `{extension}`")
            elif action == "reload":
                await self.bot.reload_extension(extension)
                await interaction.followup.send(f"✅ Reloaded `{extension}`")
            
            # Sync commands if needed (optional, but good practice)
            # await self.bot.tree.sync() 
            
        except Exception as e:
            logger.error(f"Failed to {action} extension {extension}: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Failed to {action} `{extension}`: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(CogManagementCog(bot))
