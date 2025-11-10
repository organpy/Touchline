"""Player Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

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


class AddPlayerModal(discord.ui.Modal, title="Add Player"):
    """Modal for adding a new player to the database."""

    discord_id = discord.ui.TextInput(
        label="Discord User ID",
        placeholder="e.g., 123456789",
        required=True,
        min_length=5,
        max_length=20,
    )

    display_name = discord.ui.TextInput(
        label="Player Display Name",
        placeholder="e.g., Ace, Pro Player",
        required=True,
        min_length=1,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await interaction.response.defer(ephemeral=True)

        try:
            discord_id_int = int(self.discord_id.value)
        except ValueError:
            await interaction.followup.send(
                "❌ Discord User ID must be a valid number.",
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

        # Check if player already exists
        existing = await queries.get_player_by_discord_id(supabase, discord_id_int)
        if existing:
            await interaction.followup.send(
                f"⚠️ Player with Discord ID {discord_id_int} already exists as **{existing['display_name']}**.",
                ephemeral=True,
            )
            return

        # Create new player
        try:
            player_data = {
                "discord_id": discord_id_int,
                "display_name": self.display_name.value,
            }

            response = supabase.table("players").insert(player_data).execute()

            if response.data and len(response.data) > 0:
                new_player = response.data[0]
                embed = discord.Embed(
                    title="✅ Player Added",
                    description=f"Player **{new_player['display_name']}** successfully registered.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Discord ID", value=discord_id_int, inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)

                # Refresh cache
                try:
                    await TeamPlayerCache.refresh()
                except Exception as cache_error:
                    logger.warning(f"Failed to refresh cache after adding player: {cache_error}")

            else:
                await interaction.followup.send(
                    "❌ Failed to add player (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error adding player: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error adding player: {str(e)[:100]}",
                ephemeral=True,
            )


class LinkPlayerModal(discord.ui.Modal, title="Link Player to Discord"):
    """Modal for linking an existing player to a Discord ID."""

    discord_id = discord.ui.TextInput(
        label="Discord User ID",
        placeholder="e.g., 123456789",
        required=True,
        min_length=5,
        max_length=20,
    )

    player_name = discord.ui.TextInput(
        label="Player Display Name",
        placeholder="e.g., Ace, Pro Player",
        required=True,
        min_length=1,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission - link player to Discord ID."""
        await interaction.response.defer(ephemeral=True)

        try:
            discord_id_int = int(self.discord_id.value)
        except ValueError:
            await interaction.followup.send(
                "❌ Discord User ID must be a valid number.",
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

        # Check if Discord ID is already linked to someone
        existing = await queries.get_player_by_discord_id(supabase, discord_id_int)
        if existing:
            await interaction.followup.send(
                f"⚠️ Discord ID {discord_id_int} is already linked to **{existing['display_name']}**.",
                ephemeral=True,
            )
            return

        # Find player by display name
        try:
            response = (
                supabase.table("players")
                .select("*")
                .is_("discord_id", "null")
                .ilike("display_name", f"%{self.player_name.value}%")
                .order("display_name")
                .execute()
            )
            matching_players = response.data or []
        except Exception as e:
            logger.error(f"Error fetching players: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Failed to fetch player list.",
                ephemeral=True,
            )
            return

        if not matching_players:
            await interaction.followup.send(
                f"❌ No unlinked player found matching **{self.player_name.value}**.",
                ephemeral=True,
            )
            return

        if len(matching_players) > 1:
            # Show multiple matches
            embed = discord.Embed(
                title="Multiple Players Found",
                description=f"Found {len(matching_players)} players matching **{self.player_name.value}**:\n" +
                            "\n".join([f"• {p['display_name']}" for p in matching_players[:10]]),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Single match - proceed to linking
        player = matching_players[0]

        # Update player with Discord ID
        try:
            response = (
                supabase.table("players")
                .update({"discord_id": discord_id_int})
                .eq("id", player["id"])
                .execute()
            )

            if response.data:
                # Linking successful
                embed = discord.Embed(
                    title="✅ Player Linked",
                    description=f"**{player['display_name']}** linked to <@{discord_id_int}>",
                    color=discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

                try:
                    await TeamPlayerCache.refresh()
                except Exception as cache_error:
                    logger.warning(f"Failed to refresh cache: {cache_error}")
            else:
                await interaction.followup.send(
                    "❌ Failed to link player (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error linking player: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error linking player: {str(e)[:100]}",
                ephemeral=True,
            )


class ViewPlayersView(LazyInitView):

    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.supabase: Optional[Any] = None
        self._selected_player: Optional[Dict[str, Any]] = None
        self._players_list: list[Dict[str, Any]] = []

    async def populate(self) -> None:
        """Load available players."""
        if not self.supabase:
            return

        try:
            response = self.supabase.table("players").select("*").order("display_name").execute()
            self._players_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching players: {e}", exc_info=True)
            self._players_list = []

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handle view errors."""
        logger.error(f"Error in SetPositionView: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)


class DeactivatePlayerModal(discord.ui.Modal, title="Deactivate Player"):
    """Modal for deactivating a player."""

    player_name = discord.ui.TextInput(
        label="Player Display Name",
        placeholder="e.g., Ace",
        required=True,
        min_length=1,
        max_length=100,
    )

    reason = discord.ui.TextInput(
        label="Reason for Deactivation (Optional)",
        placeholder="e.g., Retired, Inactive",
        required=False,
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

        # Find player by name
        player = await queries.get_player_by_name(supabase, self.player_name.value)
        if not player:
            await interaction.followup.send(
                f"❌ Player **{self.player_name.value}** not found.",
                ephemeral=True,
            )
            return

        # Check if already deactivated
        if not player.get("active", True):
            await interaction.followup.send(
                f"⚠️ Player **{player['display_name']}** is already deactivated.",
                ephemeral=True,
            )
            return

        # Deactivate player
        try:
            update_data = {"active": False}
            if self.reason.value:
                update_data["deactivation_reason"] = self.reason.value

            response = (
                supabase.table("players")
                .update(update_data)
                .eq("id", player["id"])
                .execute()
            )

            if response.data:
                embed = discord.Embed(
                    title="✅ Player Deactivated",
                    description=f"Player **{player['display_name']}** has been deactivated.",
                    color=discord.Color.orange(),
                )
                if self.reason.value:
                    embed.add_field(name="Reason", value=self.reason.value, inline=False)

                await interaction.followup.send(embed=embed, ephemeral=True)

                # Refresh cache
                try:
                    await TeamPlayerCache.refresh()
                except Exception as cache_error:
                    logger.warning(f"Failed to refresh cache after deactivating player: {cache_error}")

            else:
                await interaction.followup.send(
                    "❌ Failed to deactivate player (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error deactivating player: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error deactivating player: {str(e)[:100]}",
                ephemeral=True,
            )


class ViewPlayersView(LazyInitView):
    """View for displaying all players with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 10
        self.players_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch players from database."""
        if not self.supabase:
            return

        try:
            response = self.supabase.table("players").select("*").execute()
            self.players_list = response.data or []
        except Exception as e:
            logger.error(f"Error fetching players: {e}", exc_info=True)
            self.players_list = []

    async def populate(self) -> None:
        await self.lazy_init()

    def create_embed(self) -> discord.Embed:
        """Build player list embed."""
        if not self.players_list:
            embed = discord.Embed(
                title="👤 Players",
                description="No players registered.",
                color=discord.Color.blue(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.players_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_players = self.players_list[start_idx:end_idx]

        embed = discord.Embed(
            title="👤 Players",
            description=f"Total: {len(self.players_list)} players",
            color=discord.Color.blue(),
        )

        for player in page_players:
            status = "🟢 Active" if player.get("active", True) else "🔴 Inactive"
            discord_id = f"<@{player['discord_id']}>" if player.get("discord_id") else "—"

            embed.add_field(
                name=f"{player['display_name']} {status}",
                value=f"**Discord:** {discord_id}",
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
            embed = self.create_embed()
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Go to next page."""
        await interaction.response.defer()
        total_pages = (len(self.players_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            embed = self.create_embed()
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class PlayerActionsView(AdminUIView):
    """Buttons for player management actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        manager = AdminBaseManager()
        super().__init__(manager, guild_id, timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase

    def create_embed(self) -> discord.Embed:
        """Build player management embed."""
        embed = discord.Embed(
            title="👤 Player Management",
            description="Manage players and Discord links.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• ➕ **Add Player** - Register new player\n"
                "• 🔗 **Link Discord** - Link player to Discord ID\n"
                "• ❌ **Deactivate** - Mark player as inactive\n"
                "• 👀 **View All** - List all registered players"
            ),
            inline=False,
        )
        return embed

    @discord.ui.button(label="Add Player", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_player(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open add player modal."""
        modal = AddPlayerModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Link Discord", emoji="🔗", style=discord.ButtonStyle.primary, row=0)
    async def link_player(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open link player modal."""
        modal = LinkPlayerModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Deactivate", emoji="❌", style=discord.ButtonStyle.danger, row=1)
    async def deactivate_player(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open deactivate player modal."""
        modal = DeactivatePlayerModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View All", emoji="👀", style=discord.ButtonStyle.secondary, row=1)
    async def view_all_players(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show all players with pagination."""
        await interaction.response.defer(ephemeral=True)
        view = ViewPlayersView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for player management: %s", exc)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (required but no cog class)."""
    logger.info("✅ Players module loaded (no cog class needed - provides UI views only)")
