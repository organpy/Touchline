"""Season Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord import app_commands

from db import queries, get_supabase
from cogs.admin.base import AdminBaseManager, AdminUIView
from cogs.admin.common import LazyInitView
from utils.team_player_cache import TeamPlayerCache

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class CreateSeasonModal(discord.ui.Modal, title="Create New Season"):
    """Modal for creating a new season."""

    season_name = discord.ui.TextInput(
        label="Season Name",
        placeholder="e.g., S11, Season 11, 2025 Spring",
        required=True,
        min_length=1,
        max_length=100,
    )

    division_code = discord.ui.TextInput(
        label="Division Code",
        placeholder="e.g., NBL, MBL, NCBA",
        required=True,
        min_length=2,
        max_length=50,
    )

    start_date = discord.ui.TextInput(
        label="Start Date (Optional)",
        placeholder="YYYY-MM-DD or leave blank",
        required=False,
        max_length=10,
    )

    end_date = discord.ui.TextInput(
        label="End Date (Optional)",
        placeholder="YYYY-MM-DD or leave blank",
        required=False,
        max_length=10,
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

        manager = AdminBaseManager(supabase, interaction.guild_id if interaction.guild else 0)

        # Get division by code
        divisions = await manager.get_divisions_for_guild()
        division = None
        for d in divisions:
            if d.get("code", "").upper() == self.division_code.value.upper():
                division = d
                break

        if not division:
            await interaction.followup.send(
                f"❌ Division **{self.division_code.value}** not found.\n"
                f"Available divisions: {', '.join(d.get('code', 'Unknown') for d in divisions)}",
                ephemeral=True,
            )
            return

        # Check if season with this name already exists in division
        try:
            response = (
                supabase.table("seasons")
                .select("*")
                .eq("division_id", division["id"])
                .eq("name", self.season_name.value)
                .execute()
            )
            if response.data and len(response.data) > 0:
                await interaction.followup.send(
                    f"⚠️ Season **{self.season_name.value}** already exists in {division['name']}.",
                    ephemeral=True,
                )
                return
        except Exception as e:
            logger.error(f"Error checking existing season: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Error checking for existing season.",
                ephemeral=True,
            )
            return

        # Create season
        try:
            season_data = {
                "name": self.season_name.value,
                "division_id": division["id"],
                "active": False,  # Don't auto-activate
            }

            if self.start_date.value:
                season_data["start_date"] = self.start_date.value

            if self.end_date.value:
                season_data["end_date"] = self.end_date.value

            response = supabase.table("seasons").insert(season_data).execute()

            if response.data and len(response.data) > 0:
                new_season = response.data[0]
                embed = discord.Embed(
                    title="✅ Season Created",
                    description=f"Season **{new_season['name']}** created for {division['name']}.",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Division", value=division["name"], inline=True)
                embed.add_field(name="Status", value="🔴 Inactive", inline=True)
                if self.start_date.value:
                    embed.add_field(name="Start", value=self.start_date.value, inline=True)
                if self.end_date.value:
                    embed.add_field(name="End", value=self.end_date.value, inline=True)

                await interaction.followup.send(embed=embed, ephemeral=True)

                # Refresh cache
                try:
                    await TeamPlayerCache.refresh()
                except Exception as cache_error:
                    logger.warning(f"Failed to refresh cache after creating season: {cache_error}")

            else:
                await interaction.followup.send(
                    "❌ Failed to create season (unexpected response).",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error creating season: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error creating season: {str(e)[:100]}",
                ephemeral=True,
            )


class ActivateSeasonSelect(discord.ui.Select):
    """Select menu for choosing a season to activate."""

    def __init__(self, guild_id: int, supabase: Any, divisions: list[Dict[str, Any]]) -> None:
        self.guild_id = guild_id
        self.supabase = supabase
        self.divisions = divisions
        super().__init__(
            placeholder="Choose a season to activate...",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle season selection."""
        await interaction.response.defer(ephemeral=True)

        season_id = self.values[0]

        try:
            # Get the season
            season_response = (
                self.supabase.table("seasons")
                .select("*")
                .eq("id", season_id)
                .execute()
            )

            if not season_response.data:
                await interaction.followup.send(
                    "❌ Season not found.",
                    ephemeral=True,
                )
                return

            season = season_response.data[0]

            # Deactivate other seasons in the same division
            deactivate_response = (
                self.supabase.table("seasons")
                .update({"active": False})
                .eq("division_id", season["division_id"])
                .neq("id", season_id)
                .execute()
            )

            # Activate the selected season
            activate_response = (
                self.supabase.table("seasons")
                .update({"active": True})
                .eq("id", season_id)
                .execute()
            )

            if activate_response.data:
                embed = discord.Embed(
                    title="✅ Season Activated",
                    description=f"Season **{season['name']}** is now active.",
                    color=discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

                # Refresh cache
                try:
                    await TeamPlayerCache.refresh()
                except Exception as cache_error:
                    logger.warning(f"Failed to refresh cache after activating season: {cache_error}")

            else:
                await interaction.followup.send(
                    "❌ Failed to activate season.",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error activating season: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error activating season: {str(e)[:100]}",
                ephemeral=True,
            )


class ActivateSeasonView(discord.ui.View):
    """View for selecting a season to activate."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any, divisions: list[Dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.divisions = divisions
        self.original_message: Optional[discord.Message] = None

    async def lazy_init(self) -> None:
        """Populate select menu with seasons."""
        if not self.supabase:
            return

        options = []

        try:
            for division in self.divisions:
                response = (
                    self.supabase.table("seasons")
                    .select("*")
                    .eq("division_id", division["id"])
                    .order("name", desc=False)
                    .execute()
                )

                seasons = response.data or []
                for season in seasons:
                    status = "🟢" if season.get("active") else "🔴"
                    label = f"{status} {season['name']} ({division['name']})"
                    options.append(
                        discord.SelectOption(
                            label=label[:100],
                            value=season["id"],
                        )
                    )

        except Exception as e:
            logger.error(f"Error fetching seasons: {e}", exc_info=True)

        if options:
            select = ActivateSeasonSelect(self.guild_id, self.supabase, self.divisions)
            select.options = options
            self.add_item(select)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to season management."""
        await interaction.response.defer()
        self.stop()


class ViewSeasonsView(LazyInitView):
    """View for displaying all seasons with pagination."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_page = 0
        self.per_page = 5
        self.seasons_list: list[Dict[str, Any]] = []

    async def lazy_init(self) -> None:
        """Fetch seasons from database."""
        if not self.supabase:
            return

        try:
            manager = AdminBaseManager(self.supabase, self.guild_id)
            divisions = await manager.get_divisions_for_guild()

            seasons_data = []
            for division in divisions:
                response = (
                    self.supabase.table("seasons")
                    .select("*")
                    .eq("division_id", division["id"])
                    .execute()
                )
                for season in response.data or []:
                    season["division_name"] = division.get("name", "Unknown")
                    seasons_data.append(season)

            self.seasons_list = seasons_data

        except Exception as e:
            logger.error(f"Error fetching seasons: {e}", exc_info=True)
            self.seasons_list = []

    def create_embed(self) -> discord.Embed:
        """Build seasons list embed."""
        if not self.seasons_list:
            embed = discord.Embed(
                title="📅 Seasons",
                description="No seasons created.",
                color=discord.Color.blue(),
            )
            return embed

        # Calculate pagination
        total_pages = (len(self.seasons_list) + self.per_page - 1) // self.per_page
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_seasons = self.seasons_list[start_idx:end_idx]

        embed = discord.Embed(
            title="📅 Seasons",
            description=f"Total: {len(self.seasons_list)} seasons",
            color=discord.Color.blue(),
        )

        for season in page_seasons:
            status = "🟢 Active" if season.get("active") else "🔴 Inactive"
            start = season.get("start_date") or "—"
            end = season.get("end_date") or "—"

            embed.add_field(
                name=f"{season['name']} {status}",
                value=f"**Division:** {season.get('division_name', 'Unknown')}\n"
                      f"**Start:** {start} | **End:** {end}",
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
        total_pages = (len(self.seasons_list) + self.per_page - 1) // self.per_page
        if self.current_page < total_pages - 1:
            self.current_page += 1
            await self.update_display()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Close the view."""
        await interaction.response.defer()
        self.stop()


class SeasonActionsView(AdminUIView):
    """Buttons for season management actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase

    def create_embed(self) -> discord.Embed:
        """Build season management embed."""
        embed = discord.Embed(
            title="📅 Season Management",
            description="Create and manage seasons for your divisions.",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• ➕ **Create Season** - Add a new season\n"
                "• 🟢 **Activate** - Set a season as active (one per division)\n"
                "• 📋 **View All** - List all seasons\n\n"
                "**Note:** Only one season can be active per division."
            ),
            inline=False,
        )
        return embed

    async def get_divisions_for_guild(self) -> list[Dict[str, Any]]:
        """Get divisions accessible to this guild."""
        if not self.supabase:
            return []

        try:
            manager = AdminBaseManager(self.supabase, self.guild_id)
            return await manager.get_divisions_for_guild()
        except Exception as e:
            logger.error(f"Error fetching divisions: {e}", exc_info=True)
            return []

    @discord.ui.button(label="Create Season", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def create_season(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open create season modal."""
        modal = CreateSeasonModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Activate", emoji="🟢", style=discord.ButtonStyle.success, row=0)
    async def activate_season(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Open activate season view."""
        await interaction.response.defer(ephemeral=True)

        divisions = await self.get_divisions_for_guild()
        if not divisions:
            await interaction.followup.send(
                "❌ No divisions configured for this guild.",
                ephemeral=True,
            )
            return

        view = ActivateSeasonView(self.bot, self.guild_id, self.supabase, divisions)
        await view.lazy_init()

        embed = discord.Embed(
            title="🟢 Activate Season",
            description="Select a season to activate (only one per division can be active).",
            color=discord.Color.green(),
        )

        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for activate season: %s", exc)

    @discord.ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def view_all_seasons(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Show all seasons with pagination."""
        await interaction.response.defer(ephemeral=True)
        view = ViewSeasonsView(self.bot, self.guild_id, self.supabase)
        await view.lazy_init()
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = message
        except Exception as exc:  # pragma: no cover
            logger.error("Unable to fetch original response for view seasons: %s", exc)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to admin dashboard."""
        await interaction.response.defer()
        self.stop()
