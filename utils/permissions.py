"""Who counts as an admin: one definition for every admin command and admin view.

An admin is a server administrator, or a member with the admin role set in the dashboard Settings page.
"""

from __future__ import annotations

import discord
from discord import app_commands

from utils.config_utility import ConfigUtility


async def is_admin(interaction: discord.Interaction) -> bool:
    """True for server administrators and holders of the configured admin role."""
    user = interaction.user
    if interaction.guild is None or not isinstance(user, discord.Member):
        return False
    if user.guild_permissions.administrator:
        return True
    guild_config = await ConfigUtility.get_guild_config(interaction.client, interaction.guild.id)
    return await ConfigUtility.check_admin_permissions(interaction, guild_config)


def admin_only():
    """Slash command check: refuses non-admins before the command runs.

    The refusal is answered by the bot's global error handler. Use with ``default_permissions`` only to hide the command
    from members: that setting can be overridden per server, this check cannot.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        return await is_admin(interaction)

    return app_commands.check(predicate)
