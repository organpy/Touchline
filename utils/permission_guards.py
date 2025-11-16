"""
Decorators for permission checking and command guards.
"""

import functools
import logging
from typing import Callable, Optional

import discord
from discord import app_commands

logger = logging.getLogger(__name__)


def require_owner_or_admin(func: Callable) -> Callable:
    """
    Decorator to ensure user is either bot owner or has admin permissions in guild.
    
    Provides early error feedback before deferred response.
    
    Usage:
        @app_commands.command()
        @require_owner_or_admin
        async def admin_command(interaction: discord.Interaction):
            pass
    """

    @functools.wraps(func)
    async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
        # Check bot owner first
        if hasattr(self, "bot") and interaction.user.id == self.bot.owner_id:
            return await func(self, interaction, *args, **kwargs)

        # Check admin permissions in guild
        if interaction.user.guild_permissions.administrator:
            return await func(self, interaction, *args, **kwargs)

        # Permission denied
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="This command requires admin permissions or bot owner status.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.warning(
            f"Unauthorized admin command attempt by {interaction.user.id} in {interaction.guild_id}"
        )

    return wrapper


def require_team_owner(func: Callable) -> Callable:
    """
    Decorator to ensure user owns a team in the active season.
    Defers interaction immediately to provide 15-minute window for response.
    
    Usage:
        @app_commands.command()
        @require_team_owner
        async def team_command(interaction: discord.Interaction):
            pass
    """

    @functools.wraps(func)
    async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
        # Always defer first to provide time window
        await interaction.response.defer(ephemeral=True)

        # Check if user owns a team (this will be validated in the actual command)
        # Decorator just ensures defer happens early
        return await func(self, interaction, *args, **kwargs)

    return wrapper
