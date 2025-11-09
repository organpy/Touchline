"""Audit & Reporting UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional
from datetime import datetime, timedelta
import io
import json
import csv

import discord
from discord import app_commands
from discord.ext import commands

from db import queries, get_supabase
from cogs.admin.base import AdminBaseManager, AdminUIView
from cogs.admin.common import LazyInitView
from utils.team_player_cache import TeamPlayerCache

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class ExportDataModal(discord.ui.Modal, title="Export Transaction Data"):
    """Modal for exporting audit data."""

    format_choice = discord.ui.TextInput(
        label="Format (json or csv)",
        placeholder="json",
        required=True,
        min_length=3,
        max_length=4,
    )

    date_range = discord.ui.TextInput(
        label="Days to include (0=all, >0=last N days)",
        placeholder="30",
        required=True,
        min_length=1,
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=True)

        format_str = self.format_choice.value.lower().strip()
        if format_str not in ["json", "csv"]:
            await interaction.followup.send(
                "❌ Format must be 'json' or 'csv'.",
                ephemeral=True,
            )
            return

        try:
            days = int(self.date_range.value)
        except ValueError:
            await interaction.followup.send(
                "❌ Days must be a valid number.",
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
            # Fetch transaction events
            if days > 0:
                since_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
                response = (
                    supabase.table("transaction_events")
                    .select("*")
                    .gte("created_at", since_date)
                    .order("created_at", desc=True)
                    .execute()
                )
            else:
                response = (
                    supabase.table("transaction_events")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                )

            events = response.data or []

            if not events:
                await interaction.followup.send(
                    f"⚠️ No transaction events found in the last {days} days.",
                    ephemeral=True,
                )
                return

            # Format data
            if format_str == "json":
                data = json.dumps(events, indent=2, default=str).encode("utf-8")
                filename = f"transactions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            else:  # csv
                if not events:
                    await interaction.followup.send("No data to export.", ephemeral=True)
                    return

                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=events[0].keys())
                writer.writeheader()
                writer.writerows(events)
                data = output.getvalue().encode("utf-8")
                filename = f"transactions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

            # Send file
            file = discord.File(io.BytesIO(data), filename=filename)
            embed = discord.Embed(
                title="✅ Data Exported",
                description=f"Exported {len(events)} transaction event(s) as {format_str.upper()}",
                color=discord.Color.green(),
            )
            embed.add_field(name="File", value=filename, inline=False)
            embed.add_field(
                name="Date Range",
                value=f"Last {days} days" if days > 0 else "All time",
                inline=True,
            )

            await interaction.followup.send(embed=embed, file=file, ephemeral=True)

            logger.info(
                f"Exported {len(events)} transactions to {filename} "
                f"by {interaction.user.id} (last {days} days)"
            )

        except Exception as e:
            logger.error(f"Error exporting data: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error exporting data: {str(e)[:100]}",
                ephemeral=True,
            )


class ViewActivityTimelineView(LazyInitView):
    """View for displaying activity timeline with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 4  # Smaller per_page due to embed field length
        self.events_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch transaction events from database."""
        if not self.supabase:
            return

        try:
            # Fetch last 100 events (performance optimization)
            response = (
                self.supabase.table("transaction_events")
                .select("*")
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            self.events_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching activity timeline: {e}", exc_info=True)
            self.events_list = []

    def create_embed(self) -> discord.Embed:
        """Build activity timeline embed."""
        if not self.events_list:
            embed = discord.Embed(
                title="📊 Activity Timeline",
                description="No transaction events recorded.",
                color=discord.Color.blue(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.events_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_events = self.events_list[start_idx:end_idx]

        embed = discord.Embed(
            title="📊 Activity Timeline",
            description=f"Recent transactions ({len(self.events_list)} total)",
            color=discord.Color.blue(),
        )

        for event in page_events:
            event_type = event.get("event_type", "unknown").upper()
            created = event.get("created_at", "Unknown")[:10]
            details = event.get("details", {})

            # Emoji based on event type
            type_emoji = {
                "SIGN": "✍️",
                "RELEASE": "🚪",
                "TRADE": "🔄",
                "LOAN": "📋",
                "VOID": "❌",
                "EDIT": "✏️",
            }.get(event_type, "📝")

            embed.add_field(
                name=f"{type_emoji} {event_type} - {created}",
                value=f"**Status:** {details.get('status', 'pending')}\n"
                      f"**Details:** {str(details)[:150]}...",
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
        total_pages = (len(self.events_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class ViewFlaggedActionsView(LazyInitView):
    """View for displaying flagged/suspicious actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.flagged_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch flagged transactions from database."""
        if not self.supabase:
            return

        try:
            # Fetch events with "VOID" or "EDIT" event types (flagged actions)
            response = (
                self.supabase.table("transaction_events")
                .select("*")
                .in_("event_type", ["VOID", "EDIT"])
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            self.flagged_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching flagged actions: {e}", exc_info=True)
            self.flagged_list = []

    def create_embed(self) -> discord.Embed:
        """Build flagged actions embed."""
        if not self.flagged_list:
            embed = discord.Embed(
                title="🚩 Flagged Actions",
                description="No flagged actions recorded.",
                color=discord.Color.green(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.flagged_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_actions = self.flagged_list[start_idx:end_idx]

        embed = discord.Embed(
            title="🚩 Flagged Actions (Voids & Edits)",
            description=f"Actions reviewed by admins ({len(self.flagged_list)} total)",
            color=discord.Color.orange(),
        )

        for action in page_actions:
            event_type = action.get("event_type", "unknown").upper()
            created = action.get("created_at", "Unknown")[:10]
            details = action.get("details", {})

            type_emoji = "❌" if event_type == "VOID" else "✏️"

            embed.add_field(
                name=f"{type_emoji} {event_type} - {created}",
                value=f"**Reason:** {details.get('reason', 'No reason provided')}\n"
                      f"**Target:** {details.get('transaction_id', 'Unknown')[:8]}...",
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
        total_pages = (len(self.flagged_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class AuditReportsActionsView(AdminUIView):
    """Buttons for audit & reporting management."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        manager = AdminBaseManager()
        super().__init__(manager, guild_id, timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase

    def create_embed(self) -> discord.Embed:
        """Build audit & reporting management embed."""
        embed = discord.Embed(
            title="📊 Audit & Reporting",
            description="View transaction audit logs and activity reports.",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• 📋 **Timeline** - View all transaction activity\n"
                "• 🚩 **Flagged** - Review voids and edits\n"
                "• 📥 **Export** - Download transaction data\n\n"
                "**Features:** Comprehensive audit trail and reporting"
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Timeline", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def view_timeline(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show activity timeline."""
        await interaction.response.defer(ephemeral=True)
        view = ViewActivityTimelineView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for timeline: %s", exc)

    @discord.ui.button(label="Flagged", emoji="🚩", style=discord.ButtonStyle.danger, row=0)
    async def view_flagged(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show flagged actions."""
        await interaction.response.defer(ephemeral=True)
        view = ViewFlaggedActionsView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for flagged actions: %s", exc)

    @discord.ui.button(label="Export", emoji="📥", style=discord.ButtonStyle.primary, row=0)
    async def export_data(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open export data modal."""
        modal = ExportDataModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (required but no cog class)."""
    logger.info("✅ Audit & Reporting module loaded (no cog class needed - provides UI views only)")
