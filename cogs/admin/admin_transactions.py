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
from cogs.admin.pending_offers import PendingOffersActionsView
from cogs.admin.audit_reports import AuditReportsActionsView
from cogs.admin.transaction_settings import TransactionSettingsView
from cogs.admin.transaction_blocker import TransactionBlockerSummaryView
from utils.config_utility import ConfigUtility
from utils.dashboard_metrics import DashboardMetricsView

logger = logging.getLogger(__name__)


class AdminDashboardView(discord.ui.View):
    """Main admin transactions dashboard with section buttons."""

    SECTION_DESCRIPTIONS = [
        ("🏢 Team Management", "Add, edit, delete teams and set owners"),
        ("👤 Player Management", "Register players and link Discord"),
        ("🏀 Roster Management", "Force sign/release/transfer players"),
        ("🏆 Draft Pick Management", "Seed picks, reassign ownership"),
        ("📝 Transaction Overrides", "Void, edit, or resend transaction announcements"),
        ("📅 Season Management", "Create, activate, deactivate seasons"),
        ("⏰ Pending Offers Management", "Manage signing offers, expire early"),
        ("📊 Audit & Reporting", "View activity logs, flagged actions, export data"),
        ("� Transaction Blocker", "Block teams from doing transactions mid-season"),
        ("�🛑 Transaction Controls", "Toggle signings/trades/releases, ban teams"),
    ]

    def __init__(self, bot: Any, guild_id: int, config_data: Dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.config_data = config_data
        self.original_message: Optional[discord.Message] = None

    async def _safe_defer(self, interaction: discord.Interaction) -> bool:
        """Attempt to defer the interaction; return False if it's expired."""
        try:
            await interaction.response.defer(ephemeral=True)
            return True
        except discord.NotFound:
            logger.warning(
                "Expired interaction token for admin dashboard button (user=%s)",
                getattr(interaction.user, "id", "unknown"),
            )
            # Interaction token is no longer valid; we can't respond, so bail out.
            return False

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
        if not await self._safe_defer(interaction):
            return
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
        if not await self._safe_defer(interaction):
            return
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
        if not await self._safe_defer(interaction):
            return
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
        if not await self._safe_defer(interaction):
            return
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
        if not await self._safe_defer(interaction):
            return
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
        if not await self._safe_defer(interaction):
            return
        view = SeasonActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for season management: %s", exc)

    @discord.ui.button(label="Pending Offers", emoji="⏰", style=discord.ButtonStyle.primary, row=1)
    async def pending_offers(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open pending offers management interface."""
        if not await self._safe_defer(interaction):
            return
        view = PendingOffersActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for pending offers: %s", exc)

    @discord.ui.button(label="Audit & Reporting", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
    async def audit_reports(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open audit reports interface."""
        if not await self._safe_defer(interaction):
            return
        view = AuditReportsActionsView(self.bot, interaction.guild.id, self.bot.supabase)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except AttributeError:
            pass

    @discord.ui.button(label="Transaction Blocker", emoji="🔐", style=discord.ButtonStyle.warning, row=2)
    async def transaction_blocker(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open transaction blocker interface."""
        if not await self._safe_defer(interaction):
            return
        
        from utils.config_manager import get_config_manager
        config_mgr = get_config_manager()
        config = await config_mgr.get_guild_config(interaction.guild_id)
        
        transaction_blocks = config.get("transaction_blocks", {})
        
        from db import get_supabase
        supabase = get_supabase()
        
        from cogs.admin.transaction_blocker import TransactionBlockerSummaryView
        view = TransactionBlockerSummaryView(interaction.guild_id, supabase, transaction_blocks)
        
        from utils.team_player_cache import TeamPlayerCache
        cache = TeamPlayerCache()
        all_teams = cache.get_all_teams()
        blocked_count = len(transaction_blocks)
        enabled_count = len(all_teams) - blocked_count
        
        embed = discord.Embed(
            title="🔐 Transaction Blocker",
            description="Block teams from doing signings/trades/releases mid-season",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="📊 Current Status",
            value=f"✅ Enabled: {enabled_count} teams\n🚫 Blocked: {blocked_count} teams\n📦 Total: {len(all_teams)} teams",
            inline=False,
        )
        
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message

    @discord.ui.button(label="Transaction Controls", emoji="🛑", style=discord.ButtonStyle.danger, row=3)
    async def transaction_controls(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open transaction settings interface."""
        if not await self._safe_defer(interaction):
            return
        view = TransactionSettingsView(self.bot, interaction.guild.id)
        # Initialize the view (load current settings)
        await view.initialize()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except AttributeError:
            pass

    @discord.ui.button(label="Close", emoji="❌", style=discord.ButtonStyle.secondary, row=3)
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
