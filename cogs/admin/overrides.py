"""Transaction Overrides UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from db import queries, get_supabase
from cogs.admin.base import AdminBaseManager, AdminUIView
from cogs.admin.common import LazyInitView

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class VoidTransactionModal(discord.ui.Modal, title="Void Transaction"):
    """Modal for voiding a transaction."""

    transaction_id = discord.ui.TextInput(
        label="Transaction ID",
        placeholder="e.g., uuid or reference",
        required=True,
        min_length=1,
        max_length=200,
    )

    reason = discord.ui.TextInput(
        label="Reason for Voiding",
        placeholder="e.g., Duplicate, Incorrect, Retracted",
        required=True,
        min_length=1,
        max_length=200,
    )

    reverse_roster = discord.ui.TextInput(
        label="Reverse Roster Changes? (yes/no)",
        placeholder="yes or no",
        required=True,
        min_length=2,
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=True)

        reverse = self.reverse_roster.value.lower() in ("yes", "y", "true")

        supabase = get_supabase()
        if not supabase:
            await interaction.followup.send(
                "❌ Database connection unavailable.",
                ephemeral=True,
            )
            return

        try:
            # Find transaction
            response = (
                supabase.table("transaction_events")
                .select("*")
                .ilike("id", f"%{self.transaction_id.value}%")
                .limit(1)
                .execute()
            )

            if not response.data:
                await interaction.followup.send(
                    f"❌ Transaction **{self.transaction_id.value}** not found.",
                    ephemeral=True,
                )
                return

            transaction = response.data[0]

            # Update transaction status to voided
            void_response = (
                supabase.table("transaction_events")
                .update({
                    "status": "voided",
                    "metadata": {
                        "voided_by": interaction.user.id,
                        "voided_at": datetime.utcnow().isoformat(),
                        "void_reason": self.reason.value,
                        "reversed_roster": reverse,
                    }
                })
                .eq("id", transaction["id"])
                .execute()
            )

            if void_response.data:
                embed = discord.Embed(
                    title="✅ Transaction Voided",
                    description=f"Transaction has been marked as voided.",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Reason", value=self.reason.value, inline=False)
                embed.add_field(
                    name="Roster Reversed",
                    value="Yes" if reverse else "No",
                    inline=True
                )
                embed.add_field(
                    name="Voided By",
                    value=f"<@{interaction.user.id}>",
                    inline=True
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(
                    f"Transaction {transaction['id']} voided by {interaction.user.id} "
                    f"with reason: {self.reason.value} (reverse={reverse})"
                )

            else:
                await interaction.followup.send(
                    "❌ Failed to void transaction (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error voiding transaction: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error voiding transaction: {str(e)[:100]}",
                ephemeral=True,
            )


class EditTransactionModal(discord.ui.Modal, title="Edit Transaction"):
    """Modal for editing transaction details."""

    transaction_id = discord.ui.TextInput(
        label="Transaction ID",
        placeholder="e.g., uuid or reference",
        required=True,
        min_length=1,
        max_length=200,
    )

    notes = discord.ui.TextInput(
        label="Additional Notes",
        placeholder="e.g., Updated information, corrections",
        required=True,
        min_length=1,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=True)

        supabase = get_supabase()
        if not supabase:
            await interaction.followup.send(
                "❌ Database connection unavailable.",
                ephemeral=True,
            )
            return

        try:
            # Find transaction
            response = (
                supabase.table("transaction_events")
                .select("*")
                .ilike("id", f"%{self.transaction_id.value}%")
                .limit(1)
                .execute()
            )

            if not response.data:
                await interaction.followup.send(
                    f"❌ Transaction **{self.transaction_id.value}** not found.",
                    ephemeral=True,
                )
                return

            transaction = response.data[0]

            # Update transaction notes
            update_data = {
                "metadata": {
                    **(transaction.get("metadata") or {}),
                    "admin_notes": self.notes.value,
                    "last_edited_by": interaction.user.id,
                    "last_edited_at": datetime.utcnow().isoformat(),
                }
            }

            update_response = (
                supabase.table("transaction_events")
                .update(update_data)
                .eq("id", transaction["id"])
                .execute()
            )

            if update_response.data:
                embed = discord.Embed(
                    title="✅ Transaction Updated",
                    description=f"Transaction notes have been updated.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Notes", value=self.notes.value, inline=False)
                embed.add_field(
                    name="Updated By",
                    value=f"<@{interaction.user.id}>",
                    inline=True
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(
                    f"Transaction {transaction['id']} updated by {interaction.user.id}"
                )

            else:
                await interaction.followup.send(
                    "❌ Failed to update transaction (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error updating transaction: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error updating transaction: {str(e)[:100]}",
                ephemeral=True,
            )


class ResendAnnouncementModal(discord.ui.Modal, title="Resend Announcement"):
    """Modal for resending transaction announcements."""

    transaction_id = discord.ui.TextInput(
        label="Transaction ID",
        placeholder="e.g., uuid or reference",
        required=True,
        min_length=1,
        max_length=200,
    )

    channel_id = discord.ui.TextInput(
        label="Channel ID",
        placeholder="e.g., 123456789",
        required=True,
        min_length=5,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=True)

        try:
            channel_id_int = int(self.channel_id.value)
        except ValueError:
            await interaction.followup.send(
                "❌ Channel ID must be a valid number.",
                ephemeral=True,
            )
            return

        supabase = get_supabase()
        if not supabase:
            await interaction.followup.send(
                "❌ Database connection unavailable.",
                ephemeral=True,
            )
            return

        try:
            # Find transaction
            response = (
                supabase.table("transaction_events")
                .select("*")
                .ilike("id", f"%{self.transaction_id.value}%")
                .limit(1)
                .execute()
            )

            if not response.data:
                await interaction.followup.send(
                    f"❌ Transaction **{self.transaction_id.value}** not found.",
                    ephemeral=True,
                )
                return

            transaction = response.data[0]

            # Get channel
            try:
                channel = interaction.client.get_channel(channel_id_int)
                if not channel:
                    await interaction.followup.send(
                        f"❌ Channel <#{channel_id_int}> not found or not accessible.",
                        ephemeral=True,
                    )
                    return
            except Exception as e:
                logger.error(f"Error getting channel: {e}")
                await interaction.followup.send(
                    f"❌ Error accessing channel: {str(e)[:100]}",
                    ephemeral=True,
                )
                return

            # Build announcement embed
            embed = discord.Embed(
                title="📢 Transaction Announcement (Resent)",
                description=f"Transaction ID: `{transaction['id'][:8]}`",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Type",
                value=transaction.get("transaction_type", "Unknown"),
                inline=True
            )
            embed.add_field(
                name="Status",
                value=transaction.get("status", "Unknown"),
                inline=True
            )
            embed.add_field(
                name="Original Date",
                value=transaction.get("created_at", "Unknown"),
                inline=False
            )
            embed.add_field(
                name="Resent By",
                value=f"<@{interaction.user.id}>",
                inline=False
            )
            embed.set_footer(text="This is a resend of a previous announcement.")

            # Send announcement
            try:
                await channel.send(embed=embed)

                response_embed = discord.Embed(
                    title="✅ Announcement Resent",
                    description=f"Announcement posted to <#{channel_id_int}>.",
                    color=discord.Color.green(),
                )

                await interaction.followup.send(embed=response_embed, ephemeral=True)

                logger.info(
                    f"Transaction {transaction['id']} announcement resent by "
                    f"{interaction.user.id} to channel {channel_id_int}"
                )

            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ Bot doesn't have permission to send messages in <#{channel_id_int}>.",
                    ephemeral=True,
                )
            except Exception as e:
                logger.error(f"Error sending announcement: {e}")
                await interaction.followup.send(
                    f"❌ Error sending announcement: {str(e)[:100]}",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error resending announcement: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error resending announcement: {str(e)[:100]}",
                ephemeral=True,
            )


class ViewTransactionHistoryView(LazyInitView):
    """View for displaying transaction history with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.transactions_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch transactions from database."""
        if not self.supabase:
            return

        try:
            response = (
                self.supabase.table("transaction_events")
                .select("*")
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            self.transactions_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching transactions: {e}", exc_info=True)
            self.transactions_list = []

    def create_embed(self) -> discord.Embed:
        """Build transaction history embed."""
        if not self.transactions_list:
            embed = discord.Embed(
                title="📋 Transaction History",
                description="No transactions recorded.",
                color=discord.Color.blue(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.transactions_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_transactions = self.transactions_list[start_idx:end_idx]

        embed = discord.Embed(
            title="📋 Transaction History",
            description=f"Total: {len(self.transactions_list)} transactions",
            color=discord.Color.blue(),
        )

        for txn in page_transactions:
            status_emoji = {
                "pending": "⏳",
                "completed": "✅",
                "failed": "❌",
                "voided": "🚫",
            }.get(txn.get("status", "unknown"), "❓")

            txn_id_short = txn["id"][:8] if txn.get("id") else "Unknown"
            txn_type = txn.get("transaction_type", "Unknown")
            status = txn.get("status", "Unknown").capitalize()
            created = txn.get("created_at", "Unknown")[:10]

            embed.add_field(
                name=f"{status_emoji} {txn_type}",
                value=f"**ID:** `{txn_id_short}`\n"
                      f"**Status:** {status}\n"
                      f"**Date:** {created}",
                inline=False,
            )

        embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Go to previous page."""
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_display()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Go to next page."""
        await interaction.response.defer()
        total_pages = (len(self.transactions_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class OverridesActionsView(AdminUIView):
    """Buttons for transaction override actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase

    def create_embed(self) -> discord.Embed:
        """Build transaction overrides embed."""
        embed = discord.Embed(
            title="📝 Transaction Overrides",
            description="Manage and override transactions.",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• 🚫 **Void Transaction** - Mark transaction as void\n"
                "• ✏️ **Edit Details** - Update transaction notes\n"
                "• 📢 **Resend Announcement** - Post to channel\n"
                "• 📋 **View History** - See all transactions\n\n"
                "**Use with caution:** These actions affect recorded data."
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Void Transaction", emoji="🚫", style=discord.ButtonStyle.danger, row=0)
    async def void_transaction(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open void transaction modal."""
        modal = VoidTransactionModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Details", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit_transaction(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open edit transaction modal."""
        modal = EditTransactionModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Resend Announcement", emoji="📢", style=discord.ButtonStyle.primary, row=0)
    async def resend_announcement(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open resend announcement modal."""
        modal = ResendAnnouncementModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View History", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def view_history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show transaction history with pagination."""
        await interaction.response.defer(ephemeral=True)
        view = ViewTransactionHistoryView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for transaction history: %s", exc)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (required but no cog class)."""
    logger.info("✅ Overrides module loaded (no cog class needed - provides UI views only)")
