"""``/dashboard``: the admin panel where the whole league is run."""

from __future__ import annotations

import logging
from typing import Any, Optional, Type

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase
from utils.config_utility import ConfigUtility
from utils.permissions import is_admin

from .core import DashboardPage, DashboardSession
from .home import HomePage

logger = logging.getLogger(__name__)


async def check_dashboard_access(bot: Any, interaction: discord.Interaction) -> bool:
    """Admins only, and only in the admin channel when one is configured. Replies when refused."""
    if not interaction.guild:
        await interaction.response.send_message("❌ The dashboard only works inside a server.", ephemeral=True)
        return False
    # Admin first: a member without rights is not told which channel is the admin channel.
    if not await is_admin(interaction):
        await ConfigUtility.send_permission_denied_message(interaction)
        return False
    guild_config = await ConfigUtility.get_guild_config(bot, interaction.guild.id)
    allowed, channel_embed = await ConfigUtility.check_admin_channel_restriction(interaction, guild_config)
    if not allowed:
        await interaction.response.send_message(embed=channel_embed, ephemeral=True)
        return False
    return True


async def open_dashboard(
    bot: Any,
    interaction: discord.Interaction,
    section: Optional[Type[DashboardPage]] = None,
) -> Optional[DashboardSession]:
    """Send a new dashboard message, optionally opened at a section (Back returns to Home)."""
    if not await check_dashboard_access(bot, interaction):
        return None
    session = DashboardSession(bot, interaction.guild.id, interaction.user.id, get_supabase())
    home = HomePage(session)
    session.stack.append(home)
    page: DashboardPage = home
    if section is not None and section is not HomePage:
        page = section(session)
        session.stack.append(page)
    embed = await page.render()
    await interaction.response.send_message(embed=embed, view=page, ephemeral=True)
    logger.info("Dashboard opened by %s in guild %s (%s)", interaction.user.id, interaction.guild.id, type(page).__name__)
    return session


class DashboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="dashboard", description="[ADMIN] Run the league: divisions, teams, seasons and playoffs")
    async def dashboard(self, interaction: discord.Interaction) -> None:
        await open_dashboard(self.bot, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardCog(bot))
    logger.info("✅ DashboardCog loaded")
