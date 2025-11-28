"""Trade Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, List
from datetime import datetime, timezone

import discord

from db import get_supabase
from db.transactions_repository import TransactionsRepository
from cogs.admin.common import LazyInitView
from utils.team_player_cache import TeamPlayerCache

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TradeManagementView(discord.ui.View):
    """Main view for managing trades - view pending, cancel, etc."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.repo = TransactionsRepository(supabase)
        self.original_message: Optional[discord.Message] = None

    def create_embed(self) -> discord.Embed:
        """Build dark minimalistic trade management embed."""
        embed = discord.Embed(
            title="Trade Management",
            description="View and manage trades.",
            color=0x2B2D31,  # Dark theme
        )
        embed.add_field(
            name="Actions",
            value=(
                "**Pending** - View pending trades\n"
                "**History** - View completed trades\n"
                "**Cancel** - Force cancel trade"
            ),
            inline=False,
        )
        return embed

    async def update_display(self) -> None:
        """Refresh the embed message."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:
            logger.error("Failed to update trade management embed: %s", exc)

    @discord.ui.button(label="Pending", style=discord.ButtonStyle.secondary, row=0)
    async def view_pending(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """View pending trade proposals."""
        view = ViewPendingTradesView(self.bot, self.guild_id, self.supabase)
        await view.prepare()
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, row=0)
    async def view_history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """View trade history."""
        view = ViewTradeHistoryView(self.bot, self.guild_id, self.supabase)
        await view.prepare()
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=0)
    async def cancel_trade(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Cancel a pending trade."""
        modal = CancelTradeModal(self.supabase, self.repo)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to main dashboard."""
        await interaction.response.defer()
        self.stop()


class ViewPendingTradesView(LazyInitView):
    """View for displaying pending trade proposals with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.trades_list: List[Dict[str, Any]] = []

    async def populate(self) -> None:
        """Fetch pending trades from database."""
        if not self.supabase:
            return

        try:
            response = (
                self.supabase.table("trade_proposals")
                .select("*, trade_assets(*)")
                .eq("status", "pending")
                .order("created_at", desc=True)
                .execute()
            )
            self.trades_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching pending trades: {e}", exc_info=True)
            self.trades_list = []

    def create_embed(self) -> discord.Embed:
        """Build pending trades embed."""
        if not self.trades_list:
            embed = discord.Embed(
                title="Pending Trade Proposals",
                description="No pending trade proposals found.",
                color=discord.Color.blue(),
            )
            return embed

        total_pages = max(1, (len(self.trades_list) + self.per_page - 1) // self.per_page)
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_trades = self.trades_list[start_idx:end_idx]

        embed = discord.Embed(
            title="Pending Trade Proposals",
            description=f"Total: {len(self.trades_list)} pending trade(s)",
            color=discord.Color.blue(),
        )

        cache = TeamPlayerCache()

        for trade in page_trades:
            trade_id_short = trade["id"][:8] if trade.get("id") else "Unknown"
            created = trade.get("created_at", "Unknown")[:10]

            # Get team names
            initiating_team_id = trade.get("initiating_team_id")
            counterparty_team_id = trade.get("counterparty_team_id")

            initiating_team = cache.get_team(initiating_team_id) if initiating_team_id else None
            counterparty_team = cache.get_team(counterparty_team_id) if counterparty_team_id else None

            init_name = initiating_team.get("name", "Unknown") if initiating_team else "Unknown"
            counter_name = counterparty_team.get("name", "Unknown") if counterparty_team else "Unknown"

            # Count assets
            assets = trade.get("trade_assets", [])
            outbound = len([a for a in assets if a.get("direction") == "outbound"])
            inbound = len([a for a in assets if a.get("direction") == "inbound"])

            embed.add_field(
                name=f"{init_name} to {counter_name}",
                value=(
                    f"ID: `{trade_id_short}`\n"
                    f"Created: {created}\n"
                    f"Assets: {outbound} out, {inbound} in"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages}")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_display()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        total_pages = max(1, (len(self.trades_list) + self.per_page - 1) // self.per_page)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


class ViewTradeHistoryView(LazyInitView):
    """View for displaying trade history with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.trades_list: List[Dict[str, Any]] = []

    async def populate(self) -> None:
        """Fetch trade history from database."""
        if not self.supabase:
            return

        try:
            response = (
                self.supabase.table("trade_proposals")
                .select("*, trade_assets(*)")
                .neq("status", "pending")
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            self.trades_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching trade history: {e}", exc_info=True)
            self.trades_list = []

    def create_embed(self) -> discord.Embed:
        """Build trade history embed."""
        if not self.trades_list:
            embed = discord.Embed(
                title="Trade History",
                description="No trade history found.",
                color=discord.Color.blue(),
            )
            return embed

        total_pages = max(1, (len(self.trades_list) + self.per_page - 1) // self.per_page)
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_trades = self.trades_list[start_idx:end_idx]

        embed = discord.Embed(
            title="Trade History",
            description=f"Showing last {len(self.trades_list)} trades",
            color=discord.Color.blue(),
        )

        cache = TeamPlayerCache()

        status_labels = {
            "executed": "Executed",
            "declined": "Declined",
            "cancelled": "Cancelled",
            "expired": "Expired",
        }

        for trade in page_trades:
            trade_id_short = trade["id"][:8] if trade.get("id") else "Unknown"
            status = trade.get("status", "unknown")
            status_label = status_labels.get(status, status.title())
            created = trade.get("created_at", "Unknown")[:10]

            initiating_team_id = trade.get("initiating_team_id")
            counterparty_team_id = trade.get("counterparty_team_id")

            initiating_team = cache.get_team(initiating_team_id) if initiating_team_id else None
            counterparty_team = cache.get_team(counterparty_team_id) if counterparty_team_id else None

            init_name = initiating_team.get("name", "Unknown") if initiating_team else "Unknown"
            counter_name = counterparty_team.get("name", "Unknown") if counterparty_team else "Unknown"

            embed.add_field(
                name=f"[{status_label}] {init_name} to {counter_name}",
                value=(
                    f"ID: `{trade_id_short}`\n"
                    f"Date: {created}"
                ),
                inline=False,
            )

        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages}")
        return embed

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_display()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        total_pages = max(1, (len(self.trades_list) + self.per_page - 1) // self.per_page)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


class CancelTradeModal(discord.ui.Modal, title="Cancel Trade"):
    """Modal for cancelling a pending trade."""

    trade_id = discord.ui.TextInput(
        label="Trade ID",
        placeholder="Enter first 8 characters of trade ID",
        required=True,
        min_length=4,
        max_length=36,
    )

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why is this trade being cancelled?",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, supabase: Any, repo: TransactionsRepository) -> None:
        super().__init__()
        self.supabase = supabase
        self.repo = repo

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            trade_input = self.trade_id.value.strip()
            trade = None

            # Try full UUID first
            if len(trade_input) == 36:
                response = (
                    self.supabase.table("trade_proposals")
                    .select("*")
                    .eq("id", trade_input)
                    .limit(1)
                    .execute()
                )
                trade = response.data[0] if response.data else None
            else:
                # Prefix search
                lookup_response = (
                    self.supabase.table("trade_proposals")
                    .select("*")
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .limit(100)
                    .execute()
                )
                for candidate in lookup_response.data or []:
                    if isinstance(candidate.get("id"), str) and candidate["id"].startswith(trade_input):
                        trade = candidate
                        break

            if not trade:
                await interaction.followup.send(
                    f"Trade `{trade_input}` not found.",
                    ephemeral=True,
                )
                return

            if trade.get("status") != "pending":
                await interaction.followup.send(
                    f"Trade is already {trade.get('status')} and cannot be cancelled.",
                    ephemeral=True,
                )
                return

            # Cancel the trade
            metadata = {"cancelled_by": interaction.user.id}
            if self.reason.value:
                metadata["cancel_reason"] = self.reason.value

            success = self.repo.update_trade_status(trade["id"], "cancelled", metadata)

            if success:
                cache = TeamPlayerCache()
                init_team = cache.get_team(trade.get("initiating_team_id"))
                counter_team = cache.get_team(trade.get("counterparty_team_id"))

                init_name = init_team.get("name", "Unknown") if init_team else "Unknown"
                counter_name = counter_team.get("name", "Unknown") if counter_team else "Unknown"

                embed = discord.Embed(
                    title="Trade Cancelled",
                    description=f"Trade between {init_name} and {counter_name} has been cancelled.",
                    color=discord.Color.orange(),
                )
                if self.reason.value:
                    embed.add_field(name="Reason", value=self.reason.value, inline=False)
                embed.add_field(name="Cancelled By", value=interaction.user.mention, inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(
                    "Trade %s cancelled by %s: %s",
                    trade["id"],
                    interaction.user.id,
                    self.reason.value or "No reason"
                )
            else:
                await interaction.followup.send(
                    "Failed to cancel trade. Check server logs.",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error cancelling trade: {e}", exc_info=True)
            await interaction.followup.send(
                f"Error cancelling trade: {str(e)[:100]}",
                ephemeral=True,
            )


# No cog class - this module provides UI views only
async def setup(bot):
    """Required setup function for discord.py extension loading."""
    logger.info("✅ Trades module loaded (no cog class needed - provides UI views only)")
