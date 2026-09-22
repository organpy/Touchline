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
from db.db_utils import execute_async

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class ResendOfferModal(discord.ui.Modal, title="Resend Signing Offer"):
    """Modal for resending a pending signing offer to the player."""

    offer_id = discord.ui.TextInput(
        label="Offer ID",
        placeholder="e.g., uuid or reference",
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
            offer_input = self.offer_id.value.strip()
            offer = None

            # Try full UUID first
            if len(offer_input) == 36:
                response = await execute_async(
                    supabase.table("pending_signings")
                    .select("*")
                    .eq("id", offer_input)
                    .limit(1)
                )
                offer = response.data[0] if response.data else None
            else:
                # Prefix search
                lookup_response = await execute_async(
                    supabase.table("pending_signings")
                    .select("*")
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .limit(200)
                )
                for candidate in lookup_response.data or []:
                    if isinstance(candidate.get("id"), str) and candidate["id"].startswith(offer_input):
                        offer = candidate
                        break

            if not offer:
                await interaction.followup.send(
                    f"❌ Offer **{self.offer_id.value}** not found.",
                    ephemeral=True,
                )
                return

            if offer.get("status") != "pending":
                await interaction.followup.send(
                    f"⚠️ Offer is **{offer.get('status')}** and cannot be resent.",
                    ephemeral=True,
                )
                return

            # Get player and team info
            player_id = offer.get("player_id")
            team_id = offer.get("team_id")
            season_id = offer.get("season_id")

            player_resp = await execute_async(supabase.table("players").select("*").eq("id", player_id).limit(1))
            team_resp = await execute_async(supabase.table("teams").select("*").eq("id", team_id).limit(1))
            season_resp = await execute_async(supabase.table("seasons").select("*").eq("id", season_id).limit(1))

            if not player_resp.data or not team_resp.data or not season_resp.data:
                await interaction.followup.send(
                    "❌ Could not fetch player, team, or season information.",
                    ephemeral=True,
                )
                return

            player = player_resp.data[0]
            team = team_resp.data[0]
            season = season_resp.data[0]
            player_discord_id = player.get("discord_id")

            if not player_discord_id:
                await interaction.followup.send(
                    f"❌ Player {player.get('display_name')} has no Discord ID linked.",
                    ephemeral=True,
                )
                return

            try:
                player_member = await interaction.client.fetch_user(player_discord_id)
            except discord.NotFound:
                await interaction.followup.send(
                    f"❌ Could not find Discord user for player {player.get('display_name')}.",
                    ephemeral=True,
                )
                return

            # Rebuild the offer embed
            from utils.team_display import format_team_label_from_record
            from utils.embed_builder import EmbedBuilder
            from utils.embed_assets import build_team_logo_payload

            team_label = format_team_label_from_record(team)
            expires_at = offer.get("expires_at")
            expiry_text = f"<t:{int(datetime.fromisoformat(expires_at.replace('Z', '+00:00')).timestamp())}:R>" if expires_at else "No expiry"

            files, thumbnail = build_team_logo_payload(team.get("code"))

            league_code = None
            if season_id:
                try:
                    div_resp = await execute_async(supabase.table("seasons").select("division_id").eq("id", season_id).limit(1))
                    if div_resp.data:
                        division_id = div_resp.data[0].get("division_id")
                        if division_id:
                            div_resp2 = await execute_async(supabase.table("divisions").select("league_type").eq("id", division_id).limit(1))
                            if div_resp2.data:
                                league_code = div_resp2.data[0].get("league_type")
                except Exception:
                    pass

            builder = EmbedBuilder(
                title="Signing Offer (Resent)",
                description=f"{player_member.mention}, {team_label} would like to sign you!",
                team_code=team.get("code"),
                division_code=league_code,
            )
            builder.add_league_field(league_code)
            builder.add_field("Season", season.get("name", "Unknown"), inline=True)
            builder.add_field("Player", player.get("display_name"), inline=True)
            builder.add_field("Team", team_label, inline=True)
            builder.add_field("Expires", expiry_text, inline=False)
            builder.add_timestamp()
            if team.get("code"):
                builder.add_team_image(team.get("code"), position="thumbnail")
            builder.files.extend(files)
            embed, all_files = builder.build_with_files()

            # Send to player
            try:
                await player_member.send(embed=embed, files=all_files or None)
                await interaction.followup.send(
                    f"✅ Resent offer to {player.get('display_name')}.",
                    ephemeral=True,
                )
                logger.info(f"Resent offer {offer['id']} to {player_discord_id}")
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ Could not DM {player.get('display_name')} - they may have DMs disabled.",
                    ephemeral=True,
                )
            except Exception as e:
                logger.error(f"Error sending resend offer DM: {e}")
                await interaction.followup.send(
                    f"❌ Error sending offer: {str(e)[:100]}",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error resending offer: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error resending offer: {str(e)[:100]}",
                ephemeral=True,
            )


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
            offer_input = self.offer_id.value.strip()
            offer = None

            if offer_input and len(offer_input) == 36:
                response = await execute_async(
                    supabase.table("pending_signings")
                    .select("*")
                    .eq("id", offer_input)
                    .limit(1)
                )
                offer = response.data[0] if response.data else None
            else:
                lookup_response = await execute_async(
                    supabase.table("pending_signings")
                    .select("*")
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .limit(200)
                )
                for candidate in lookup_response.data or []:
                    if isinstance(candidate.get("id"), str) and candidate["id"].startswith(offer_input):
                        offer = candidate
                        break

            if not offer:
                await interaction.followup.send(
                    f"❌ Offer **{self.offer_id.value}** not found. Use a full UUID or first 8 characters.",
                    ephemeral=True,
                )
                return

            # Check if already expired
            if offer.get("status") != "pending":
                await interaction.followup.send(
                    f"⚠️ Offer is already **{offer.get('status')}**.",
                    ephemeral=True,
                )
                return

            # Mark as expired
            expire_response = await execute_async(
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
            response = await execute_async(
                self.supabase.table("pending_signings")
                .select("*, players!left(id, display_name, discord_id), teams!left(id, name, code)")
                .eq("status", "pending")
                .order("created_at", desc=True)
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
        """Build dark minimalistic pending offers embed."""
        embed = discord.Embed(
            title="Signing Offers",
            description="Manage pending player offers.",
            color=0x2B2D31,  # Dark theme
        )
        embed.add_field(
            name="Actions",
            value=(
                "**View** - List pending offers\n"
                "**Expire** - Mark as expired\n"
                "**Resend** - Re-notify player"
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="View", style=discord.ButtonStyle.secondary, row=0)
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

    @discord.ui.button(label="Expire", style=discord.ButtonStyle.danger, row=0)
    async def expire_offer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open expire offer modal."""
        modal = ExpireOfferModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Resend", style=discord.ButtonStyle.secondary, row=0)
    async def resend_offer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Resend offer notification to player."""
        modal = ResendOfferModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (required but no cog class)."""
    logger.info("✅ Pending Offers module loaded (no cog class needed - provides UI views only)")
