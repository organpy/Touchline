"""Transaction blocker management - disable teams from doing transactions mid-season."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
from utils.config_manager import get_config_manager
from utils.config_utility import ConfigUtility
from db.db_utils import execute_async
from utils.league_cache import LeagueCache

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class TransactionBlockerPaginatedView(discord.ui.View):
    """Paginated view to manage transaction blocks for all teams."""

    def __init__(
        self,
        guild_id: int,
        supabase: Any,
        current_blocks: Dict[str, bool],
    ):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_blocks = current_blocks.copy()
        self.pending_changes: Dict[str, bool] = {}
        self.message: Optional[discord.Message] = None
        self.current_page = 0

        # Load teams organized by division
        cache = TeamPlayerCache()
        all_teams = cache.get_all_teams()

        # Group by division
        self.divisions: List[Dict[str, Any]] = []
        divisions_dict: Dict[str, List[Dict[str, Any]]] = {}

        for team in all_teams:
            div_id = team.get("division_id")
            if div_id not in divisions_dict:
                divisions_dict[div_id] = []
            divisions_dict[div_id].append(team)

        # Convert to list of divisions, in tier order (unassigned teams last)
        def tier(div_id: Optional[str]) -> int:
            return (LeagueCache.division(div_id) or {}).get("tier") or 10_000

        for div_id in sorted(divisions_dict, key=tier):
            teams = divisions_dict[div_id]
            div_name = (LeagueCache.division(div_id) or {}).get("name") or "Unassigned"
            sorted_teams = sorted(teams, key=lambda t: t.get("code", ""))
            self.divisions.append({"id": div_id, "name": div_name, "teams": sorted_teams})

        self.total_pages = max(1, len(self.divisions))
        self.setup_buttons()

    def get_current_state(self, team_id: str) -> bool:
        """Get whether team can do transactions (True = enabled, False = blocked)."""
        # Check pending changes first
        if team_id in self.pending_changes:
            return self.pending_changes[team_id]
        
        # Check current blocks
        if isinstance(self.current_blocks, dict):
            # Old format (dict): True means blocked, so return opposite
            return not self.current_blocks.get(team_id, False)
        else:
            # New format (list): check if team_id is in the list
            return team_id not in self.current_blocks

    def setup_buttons(self) -> None:
        """Setup all buttons for current page."""
        self.clear_items()

        if self.total_pages > 1:
            prev_button = discord.ui.Button(
                label="Previous",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page == 0),
                custom_id="nav_previous",
                emoji="◀️",
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)

            page_button = discord.ui.Button(
                label=f"Page {self.current_page + 1}/{self.total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="page_indicator",
            )
            self.add_item(page_button)

            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page == self.total_pages - 1),
                custom_id="nav_next",
                emoji="▶️",
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

        current_division = self.divisions[self.current_page]
        toggle_div_button = discord.ui.Button(
            label=f"Toggle All {current_division['name']}",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            custom_id=f"toggle_division_{self.current_page}",
        )
        toggle_div_button.callback = self.toggle_division
        self.add_item(toggle_div_button)

        toggle_all_button = discord.ui.Button(
            label="Toggle All Teams",
            style=discord.ButtonStyle.danger,
            emoji="🌐",
            custom_id="toggle_all_global",
        )
        toggle_all_button.callback = self.toggle_all_teams
        self.add_item(toggle_all_button)

        save_button = discord.ui.Button(
            label="Save & Close",
            style=discord.ButtonStyle.success,
            emoji="💾",
            custom_id="save_close",
        )
        save_button.callback = self.save_and_close
        self.add_item(save_button)

        # Team buttons (max 19 per page to fit within Discord limits)
        current_division_teams = self.divisions[self.current_page]["teams"]
        for team in current_division_teams[:19]:
            team_id = team.get("id", "")
            is_enabled = self.get_current_state(team_id)
            button_style = discord.ButtonStyle.success if is_enabled else discord.ButtonStyle.danger

            button = discord.ui.Button(
                label=team.get("code", "?"),
                style=button_style,
                custom_id=f"toggle_team_{team_id}",
                emoji="✅" if is_enabled else "🚫",
            )
            button.callback = self.create_team_toggle_callback(team_id)
            self.add_item(button)

    def create_team_toggle_callback(self, team_id: str):
        """Create callback for team toggle button."""

        async def team_toggle_callback(interaction: discord.Interaction):
            current_state = self.get_current_state(team_id)
            new_state = not current_state
            self.pending_changes[team_id] = new_state
            await self.update_view_safe(interaction)

        return team_toggle_callback

    async def previous_page(self, interaction: discord.Interaction):
        """Go to previous page."""
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_view_safe(interaction)

    async def next_page(self, interaction: discord.Interaction):
        """Go to next page."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_view_safe(interaction)

    async def toggle_division(self, interaction: discord.Interaction):
        """Toggle all teams in current division."""
        current_division = self.divisions[self.current_page]
        team_ids = [team.get("id", "") for team in current_division["teams"]]

        division_states = [self.get_current_state(tid) for tid in team_ids]
        has_enabled = any(division_states)
        new_state = not has_enabled

        for team_id in team_ids:
            self.pending_changes[team_id] = new_state

        await self.update_view_safe(interaction)

    async def toggle_all_teams(self, interaction: discord.Interaction):
        """Toggle all teams globally."""
        all_team_ids = []
        for division in self.divisions:
            for team in division["teams"]:
                all_team_ids.append(team.get("id", ""))

        current_states = [self.get_current_state(tid) for tid in all_team_ids]
        has_enabled = any(current_states)
        new_state = not has_enabled

        for team_id in all_team_ids:
            self.pending_changes[team_id] = new_state

        await self.update_view_safe(interaction)

    async def save_and_close(self, interaction: discord.Interaction):
        """Save all pending changes to database."""
        try:
            await interaction.response.defer(ephemeral=True)

            if not self.supabase:
                await interaction.followup.send(
                    "❌ Supabase client unavailable.",
                    ephemeral=True,
                )
                return

            if self.pending_changes:
                logger.info(
                    "📝 Saving transaction blocker changes for guild %s", self.guild_id
                )
                logger.info("   Pending changes to apply: %s", self.pending_changes)

                upserts: List[Dict[str, Any]] = []
                removals: List[str] = []
                timestamp = datetime.now(timezone.utc).isoformat()

                for team_id, is_enabled in self.pending_changes.items():
                    logger.info(
                        "   Processing team_id=%s (type: %s), is_enabled=%s",
                        team_id,
                        type(team_id).__name__,
                        is_enabled,
                    )
                    if is_enabled:
                        removals.append(team_id)
                    else:
                        upserts.append(
                            {
                                "guild_id": self.guild_id,
                                "team_id": team_id,
                                "blocked": True,
                                "blocked_by": interaction.user.id,
                                "blocked_at": timestamp,
                                "updated_at": timestamp,
                            }
                        )

                if upserts:
                    logger.info("   ➕ Upserting %d blocked team entries", len(upserts))
                    (
                        await execute_async(self.supabase.table("transaction_blocker_entries")
                        .upsert(upserts, on_conflict="guild_id,team_id"))
                    )

                if removals:
                    logger.info("   ➖ Removing %d entries", len(removals))
                    chunk_size = 50
                    for i in range(0, len(removals), chunk_size):
                        chunk = removals[i : i + chunk_size]
                        (
                            await execute_async(self.supabase.table("transaction_blocker_entries")
                            .delete()
                            .eq("guild_id", self.guild_id)
                            .in_("team_id", chunk))
                        )

                latest_blocks = await queries.get_blocked_teams_for_guild(
                    self.supabase, self.guild_id
                )
                self.current_blocks = latest_blocks
                self.pending_changes.clear()
                logger.info("   🔄 Reloaded blocked teams: %s", latest_blocks)

            embed = discord.Embed(
                title="✅ Transaction Blocker - Changes Saved",
                description="All transaction blocks have been updated successfully.",
                color=discord.Color.green(),
            )

            blocked_set = set(
                self.current_blocks.keys()
                if isinstance(self.current_blocks, dict)
                else self.current_blocks
            )
            cache = TeamPlayerCache()
            all_teams = cache.get_all_teams()
            blocked_count = len(blocked_set)
            enabled_count = len(all_teams) - blocked_count

            embed.add_field(
                name="📊 Final Status",
                value=f"✅ Enabled: {enabled_count} | 🚫 Blocked: {blocked_count} | Total: {len(all_teams)}",
                inline=False,
            )

            embed.set_footer(text=f"Saved by {interaction.user.display_name}")

            await interaction.edit_original_response(embed=embed, view=None)

        except Exception as e:
            logger.error(f"Error saving transaction blocker changes: {e}", exc_info=True)
            try:
                embed = discord.Embed(
                    title="❌ Error Saving Changes",
                    description="There was an error saving the changes. Please try again.",
                    color=discord.Color.red(),
                )
                await interaction.edit_original_response(embed=embed, view=None)
            except Exception:
                pass

    async def update_view_safe(self, interaction: discord.Interaction):
        """Safely update the view."""
        try:
            self.setup_buttons()
            embed = self.create_embed()

            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.defer(ephemeral=True)
                await interaction.edit_original_response(embed=embed, view=self)

        except discord.NotFound:
            logger.warning("Interaction expired, unable to update view")
        except Exception as e:
            logger.error(f"Error updating transaction blocker view: {e}")

    def create_embed(self) -> discord.Embed:
        """Create the embed for current page."""
        current_division = self.divisions[self.current_page]

        embed = discord.Embed(
            title="🔐 Transaction Blocker Manager",
            description=f"Managing **{current_division['name']}** - Page {self.current_page + 1} of {self.total_pages}",
            color=discord.Color.blue(),
        )

        enabled_teams = []
        blocked_teams = []

        for team in current_division["teams"]:
            team_id = team.get("id", "")
            team_code = team.get("code", "?")
            is_enabled = self.get_current_state(team_id)

            if is_enabled:
                enabled_teams.append(team_code)
            else:
                blocked_teams.append(team_code)

        if enabled_teams:
            enabled_text = " • ".join(enabled_teams)
            embed.add_field(
                name=f"✅ Enabled in {current_division['name']}",
                value=enabled_text[:1024],
                inline=False,
            )

        if blocked_teams:
            blocked_text = " • ".join(blocked_teams)
            embed.add_field(
                name=f"🚫 Blocked in {current_division['name']}",
                value=blocked_text[:1024],
                inline=False,
            )

        # Calculate global summary
        blocked_set = set(self.current_blocks.keys() if isinstance(self.current_blocks, dict) else self.current_blocks)
        for team_id, is_enabled in self.pending_changes.items():
            if is_enabled:
                blocked_set.discard(team_id)
            else:
                blocked_set.add(team_id)
        
        cache = TeamPlayerCache()
        all_teams_global = cache.get_all_teams()
        all_blocked = len(blocked_set)
        all_enabled = len(all_teams_global) - all_blocked

        embed.add_field(
            name="🌐 Global Summary",
            value=f"✅ Enabled: {all_enabled} | 🚫 Blocked: {all_blocked} | Total: {len(all_teams_global)} teams",
            inline=False,
        )

        changes_text = (
            f"{len(self.pending_changes)} unsaved changes"
            if self.pending_changes
            else "No pending changes"
        )
        embed.add_field(
            name="📋 Instructions",
            value=f"• ✅ Green buttons = Transactions enabled\n• 🚫 Red buttons = Transactions blocked\n• Click buttons to toggle states\n• **{changes_text}**\n• Use **Save & Close** when finished",
            inline=False,
        )

        embed.set_footer(
            text="Click team codes to toggle • 'Save & Close' to commit all changes"
        )

        return embed

    async def on_timeout(self):
        """Handle timeout."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        if self.message:
            try:
                embed = discord.Embed(
                    title="⏰ Transaction Blocker - Timed Out",
                    description="The transaction blocker manager has timed out. Changes were not saved.",
                    color=discord.Color.orange(),
                )
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass


class TransactionBlockerSummaryView(discord.ui.View):
    """Summary view for transaction blocker with manage button."""

    def __init__(self, guild_id: int, supabase: Any, current_blocks: Any):  # Can be dict or list
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.supabase = supabase
        self.current_blocks = current_blocks
        self.message: Optional[discord.Message] = None

        manage_button = discord.ui.Button(
            label="Manage Transaction Blocks",
            style=discord.ButtonStyle.primary,
            emoji="⚙️",
            custom_id="open_blocker_manager",
        )
        manage_button.callback = self.open_manager
        self.add_item(manage_button)

    async def open_manager(self, interaction: discord.Interaction):
        """Open the detailed manager."""
        latest_blocks = await queries.get_blocked_teams_for_guild(self.supabase, self.guild_id)
        detailed_view = TransactionBlockerPaginatedView(
            self.guild_id, self.supabase, latest_blocks
        )
        embed = detailed_view.create_embed()

        await interaction.response.edit_message(embed=embed, view=detailed_view)
        detailed_view.message = await interaction.original_response()


class TransactionBlockerCog(commands.Cog):
    """Cog for managing transaction blocks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()

    @app_commands.command(
        name="transaction_blocker",
        description="[ADMIN] Block teams from doing signings/trades/releases mid-season",
    )
    async def transaction_blocker(self, interaction: discord.Interaction) -> None:
        """Open transaction blocker management interface."""
        await interaction.response.defer(ephemeral=True)

        config_mgr = get_config_manager()
        config = await config_mgr.get_guild_config(interaction.guild_id)

        # Check admin permissions
        if not await ConfigUtility.check_admin_permissions(interaction, config):
            await interaction.followup.send(
                "❌ You don't have permission to access this command.",
                ephemeral=True,
            )
            return

        # Get current blocked teams from dedicated table
        banned_teams = await queries.get_blocked_teams_for_guild(self.supabase, interaction.guild_id)

        # Create summary embed
        cache = TeamPlayerCache()
        all_teams = cache.get_all_teams()
        blocked_count = len(banned_teams)
        enabled_count = len(all_teams) - blocked_count

        embed = discord.Embed(
            title="🔐 Transaction Blocker - Overview",
            description="Manage which teams can perform transactions (signings, trades, releases)",
            color=discord.Color.blue(),
        )

        embed.add_field(
            name="📊 Current Status",
            value=f"✅ Enabled: {enabled_count} teams\n🚫 Blocked: {blocked_count} teams\n📦 Total: {len(all_teams)} teams",
            inline=False,
        )

        embed.add_field(
            name="ℹ️ What This Does",
            value="When a team is blocked:\n• Players cannot sign\n• Players cannot be traded\n• Players cannot be released\n• Only admins can force these actions",
            inline=False,
        )

        embed.add_field(
            name="🎮 Next Steps",
            value="Click **Manage Transaction Blocks** to control individual teams.",
            inline=False,
        )

        embed.set_footer(text="Block teams mid-season to prevent roster changes • Timeout: 5 minutes")

        view = TransactionBlockerSummaryView(interaction.guild_id, self.supabase, banned_teams)

        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message


async def setup(bot: commands.Bot) -> None:
    """Setup the cog."""
    await bot.add_cog(TransactionBlockerCog(bot))
    logger.info("✅ TransactionBlockerCog loaded")
