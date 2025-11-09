"""Admin transactions management cog with unified UI dashboard."""

import logging
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.admin.teams import TeamActionsView
from cogs.admin.rosters import RosterActionsView
from cogs.admin.picks import PicksActionsView
from cogs.admin.players import PlayerActionsView
from cogs.admin.seasons import SeasonActionsView
from cogs.admin.overrides import OverridesActionsView
from utils.config_utility import ConfigUtility

logger = logging.getLogger(__name__)


class AdminDashboardView(discord.ui.View):
    """Main admin transactions dashboard with section buttons."""

    SECTION_DESCRIPTIONS = [
        ("🏢 Team Management", "Add, edit, delete teams and set owners"),
        ("👤 Player Management", "Register players, link Discord, manage positions"),
        ("🏀 Roster Management", "Force sign/release/transfer players"),
        ("🏆 Draft Pick Management", "Seed picks, reassign ownership"),
        ("📝 Transaction Overrides", "Void, edit, or resend transaction announcements"),
        ("📅 Season Management", "Create, activate, deactivate seasons"),
        ("⏰ Pending Offers Management", "Manage signing offers, expire early"),
        ("📊 Audit & Reporting", "View activity logs, flagged actions, export data"),
    ]

    def __init__(self, bot: Any, guild_id: int, config_data: Dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.config_data = config_data
        self.original_message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure user still has admin rights."""
        has_permission = await ConfigUtility.check_admin_permissions(interaction, self.config_data)
        if not has_permission:
            await interaction.response.send_message(
                "❌ You don't have permission to access admin transactions.",
                ephemeral=True,
            )
            return False
        return True

    def create_embed(self) -> discord.Embed:
        """Build the main dashboard embed."""
        embed = discord.Embed(
            title="⚙️ Admin Transactions Dashboard",
            description="Manage teams, rosters, picks, and transactions.",
            color=discord.Color.red(),
        )

        for title, desc in self.SECTION_DESCRIPTIONS:
            embed.add_field(name=title, value=desc, inline=False)

        embed.set_footer(text="Click a button below to manage that section.")
        return embed

    async def update_display(self) -> None:
        """Refresh the main embed message."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update admin dashboard: %s", exc)

    @discord.ui.button(label="Team Management", emoji="🏢", style=discord.ButtonStyle.primary, row=0)
    async def teams(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open team management interface."""
        await interaction.response.defer(ephemeral=True)
        view = TeamActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for team management: %s", exc)

    @discord.ui.button(label="Player Management", emoji="👤", style=discord.ButtonStyle.primary, row=0)
    async def players(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open player management interface."""
        await interaction.response.defer(ephemeral=True)
        view = PlayerActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for player management: %s", exc)

    @discord.ui.button(label="Roster Management", emoji="🏀", style=discord.ButtonStyle.primary, row=0)
    async def rosters(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open roster management interface."""
        await interaction.response.defer(ephemeral=True)
        view = RosterActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for roster management: %s", exc)

    @discord.ui.button(label="Draft Picks", emoji="🏆", style=discord.ButtonStyle.primary, row=0)
    async def picks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open draft picks management interface."""
        await interaction.response.defer(ephemeral=True)
        view = PicksActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for draft picks management: %s", exc)

    @discord.ui.button(label="Transaction Overrides", emoji="📝", style=discord.ButtonStyle.primary, row=1)
    async def transactions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open transaction overrides interface."""
        await interaction.response.defer(ephemeral=True)
        view = OverridesActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for transaction overrides: %s", exc)

    @discord.ui.button(label="Season Management", emoji="📅", style=discord.ButtonStyle.primary, row=1)
    async def seasons(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open season management interface."""
        await interaction.response.defer(ephemeral=True)
        view = SeasonActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for season management: %s", exc)

    @discord.ui.button(label="Pending Offers", emoji="⏰", style=discord.ButtonStyle.primary, row=1)
    async def pending_offers(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "⏰ Pending Offers Management - Coming soon!",
            ephemeral=True,
        )

    @discord.ui.button(label="Audit & Reports", emoji="📊", style=discord.ButtonStyle.primary, row=1)
    async def audit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "📊 Audit & Reporting - Coming soon!",
            ephemeral=True,
        )

    @discord.ui.button(label="Close", emoji="❌", style=discord.ButtonStyle.secondary, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the admin dashboard."""
        embed = discord.Embed(
            title="✅ Dashboard Closed",
            description="Admin panel has been closed.",
            color=discord.Color.green(),
        )
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class AdminTransactionsCog(commands.Cog):
    """Admin commands for managing transactions, teams, and rosters."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="admin-transactions",
        description="[ADMIN] Manage teams, rosters, picks, and transactions",
    )
    async def admin_transactions(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used inside a Discord server.",
                ephemeral=True,
            )
            return

        guild_config = await ConfigUtility.get_guild_config(self.bot, interaction.guild.id)

        channel_allowed, channel_error_embed = await ConfigUtility.check_admin_channel_restriction(
            interaction, guild_config
        )
        if not channel_allowed:
            await interaction.response.send_message(embed=channel_error_embed, ephemeral=True)
            return

        has_permission = await ConfigUtility.check_admin_permissions(interaction, guild_config)
        if not has_permission:
            await ConfigUtility.send_permission_denied_message(interaction)
            return

        view = AdminDashboardView(self.bot, interaction.guild.id, guild_config)
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = await interaction.original_response()
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for admin dashboard: %s", exc)

        logger.info("📋 Admin transactions dashboard opened by %s in guild %s", interaction.user.id, interaction.guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminTransactionsCog(bot))
    logger.info("✅ AdminTransactionsCog loaded")
