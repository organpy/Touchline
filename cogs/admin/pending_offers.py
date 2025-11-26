"""Pending Offers Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional
from datetime import datetime, timedelta, timezone

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


class ExpireOfferModal(discord.ui.Modal, title="Expire Signing Offer"):
    """Modal for expiring a pending signing offer early."""

    offer_id = discord.ui.TextInput(
        label="Offer ID",
        placeholder="e.g., uuid or reference",
        required=True,
        min_length=1,
        max_length=200,
    )

    reason = discord.ui.TextInput(
        label="Reason for Expiration",
        placeholder="e.g., Withdrawn, Declined, Replaced",
        required=True,
        min_length=1,
        max_length=200,
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
            # Find pending signing by ID (support both full UUID and truncated prefix)
            offer_input = self.offer_id.value.strip()
            
            # First try exact UUID match
            response = (
                supabase.table("pending_signings")
                .select("*")
                .eq("id", offer_input)
                .limit(1)
                .execute()
            )
            
            # If not found and input is short, try pattern match for offers starting with that prefix
            if not response.data and len(offer_input) < 36:  # UUID is 36 chars
                try:
                    response = (
                        supabase.table("pending_signings")
                        .select("*")
                        .execute()
                    )
                    # Filter locally for offers starting with the prefix
                    matching_offers = [
                        o for o in (response.data or [])
                        if o.get("id", "").startswith(offer_input)
                    ]
                    if matching_offers:
                        response.data = matching_offers[:1]  # Take first match
                except Exception:
                    response.data = None

            if not response.data:
                await interaction.followup.send(
                    f"❌ Offer **{self.offer_id.value}** not found. Use a full UUID or first 8 characters.",
                    ephemeral=True,
                )
                return

            offer = response.data[0]

            # Check if already expired
            if offer.get("status") != "pending":
                await interaction.followup.send(
                    f"⚠️ Offer is already **{offer.get('status')}**.",
                    ephemeral=True,
                )
                return

            # Mark as expired
            expire_response = (
                supabase.table("pending_signings")
                .update({
                    "status": "expired",
                    "metadata": {
                        "expired_by": interaction.user.id,
                        "expired_at": datetime.utcnow().isoformat(),
                        "expiration_reason": self.reason.value,
                    }
                })
                .eq("id", offer["id"])
                .execute()
            )

            if expire_response.data:
                embed = discord.Embed(
                    title="✅ Offer Expired",
                    description=f"Signing offer has been marked as expired.",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Reason", value=self.reason.value, inline=False)
                embed.add_field(
                    name="Expired By",
                    value=f"<@{interaction.user.id}>",
                    inline=True
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(
                    f"Offer {offer['id']} expired by {interaction.user.id} "
                    f"with reason: {self.reason.value}"
                )

            else:
                await interaction.followup.send(
                    "❌ Failed to expire offer (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error expiring offer: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error expiring offer: {str(e)[:100]}",
                ephemeral=True,
            )


class ViewPendingOffersView(LazyInitView):
    """View for displaying pending signing offers with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.offers_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch pending signings from database with player/team joins."""
        if not self.supabase:
            return

        try:
            response = (
                self.supabase.table("pending_signings")
                .select("*, players!left(id, display_name, discord_id), teams!left(id, name, code)")
                .eq("status", "pending")
                .order("created_at", desc=True)
                .execute()
            )
            self.offers_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching pending offers: {e}", exc_info=True)
            self.offers_list = []

    async def populate(self) -> None:
        await self.lazy_init()

    def create_embed(self) -> discord.Embed:
        """Build pending offers embed with player/team names."""
        if not self.offers_list:
            embed = discord.Embed(
                title="⏰ Pending Signing Offers",
                description="No pending offers.",
                color=discord.Color.blue(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.offers_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_offers = self.offers_list[start_idx:end_idx]

        embed = discord.Embed(
            title="⏰ Pending Signing Offers",
            description=f"Total: {len(self.offers_list)} pending offer(s)",
            color=discord.Color.blue(),
        )

        for offer in page_offers:
            # Extract joined data
            player_data = offer.get("players", {})
            team_data = offer.get("teams", {})
            
            player_name = player_data.get("display_name", "Unknown") if player_data else "Unknown"
            team_name = team_data.get("name", "Unknown") if team_data else "Unknown"
            team_code = team_data.get("code", "?") if team_data else "?"
            
            offer_id_short = offer["id"][:8] if offer.get("id") else "Unknown"
            created = offer.get("created_at", "Unknown")[:10]
            expires = offer.get("expires_at", "Unknown")[:10]

            # Check if expired based on expires_at
            is_expired = False
            if offer.get("expires_at"):
                try:
                    expire_date = datetime.fromisoformat(offer["expires_at"].replace("Z", "+00:00"))
                    is_expired = expire_date < datetime.now(timezone.utc)
                except (ValueError, AttributeError):
                    pass

            status_emoji = "⏳" if not is_expired else "⚠️"

            embed.add_field(
                name=f"{status_emoji} {player_name} → {team_name} ({team_code})",
                value=f"**ID:** `{offer_id_short}`\n"
                      f"**Created:** {created}\n"
                      f"**Expires:** {expires}",
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
        total_pages = (len(self.offers_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class PendingOffersActionsView(AdminUIView):
    """Buttons for pending offers management actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        manager = AdminBaseManager()
        super().__init__(manager, guild_id, timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase

    def create_embed(self) -> discord.Embed:
        """Build pending offers management embed."""
        embed = discord.Embed(
            title="⏰ Pending Signing Offers",
            description="Manage pending signing offers from players.",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• 📋 **View All** - List pending offers\n"
                "• ⏱️ **Expire Offer** - Mark offer as expired\n"
                "• 🔄 **Resend** - Resend offer notification to player\n\n"
                "**Status:** Pending offers waiting player response"
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def view_all_offers(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show all pending offers with pagination."""
        await interaction.response.defer(ephemeral=True)
        view = ViewPendingOffersView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for pending offers: %s", exc)

    @discord.ui.button(label="Expire Offer", emoji="⏱️", style=discord.ButtonStyle.danger, row=0)
    async def expire_offer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open expire offer modal."""
        modal = ExpireOfferModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Resend", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def resend_offer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Resend offer notification."""
        await interaction.response.send_message(
            "🔄 Resend Offer - Feature in development\n\n"
            "This will resend the signing offer notification to the player's DM.",
            ephemeral=True,
        )

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (required but no cog class)."""
    logger.info("✅ Pending Offers module loaded (no cog class needed - provides UI views only)")
