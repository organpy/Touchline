"""Standalone /manage-picks commands for admin draft pick management.

This module provides slash commands for:
- /manage-picks seed - Create initial draft picks
- /manage-picks reassign - Move picks between teams
- /manage-picks view - List all picks for a season
- /manage-picks by-team - View picks owned by a specific team

All commands are NBL-only and require admin permissions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import queries, get_supabase
from db.transactions_repository import TransactionsRepository
from utils.config_utility import ConfigUtility
from cogs.transactions.pick_trading import (
    get_nbl_division,
    validate_nbl_season,
    get_available_picks_for_team,
    format_pick,
    NBL_LEAGUE_TYPE
)
from utils.team_player_cache import TeamPlayerCache

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


class ManagePicksCommands(commands.Cog):
    """Admin commands for managing draft picks (NBL only)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ===========================================================================
    # HELPERS
    # ===========================================================================

    async def _check_admin_permission(
        self, interaction: discord.Interaction, guild_id: int
    ) -> bool:
        """Check if user has admin permissions in this guild."""
        guild_config = await ConfigUtility.get_guild_config(self.bot, guild_id)
        has_permission = await ConfigUtility.check_admin_permissions(interaction, guild_config)
        if not has_permission:
            await interaction.followup.send(
                "❌ You don't have permission to manage draft picks.",
                ephemeral=True,
            )
        return has_permission

    async def _get_nbl_division(self, supabase) -> Optional[Dict[str, Any]]:
        """Get NBL division, with error handling."""
        try:
            nbl_div = await get_nbl_division(supabase)
            if not nbl_div:
                logger.warning("NBL division not found in database")
                return None
            return nbl_div
        except Exception as e:
            logger.error(f"Failed to fetch NBL division: {e}", exc_info=True)
            return None

    async def _get_active_nbl_season(
        self, supabase, nbl_division_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get active season for NBL division."""
        try:
            response = supabase.table("seasons").select("*").eq(
                "division_id", nbl_division_id
            ).eq(
                "active", True
            ).limit(1).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Failed to fetch active NBL season: {e}", exc_info=True)
            return None

    async def _get_all_nbl_teams(self, supabase, nbl_division_id: str) -> list:
        """Get all teams in NBL division."""
        try:
            teams = await queries.get_all_teams(supabase, nbl_division_id)
            return teams or []
        except Exception as e:
            logger.error(f"Failed to fetch NBL teams: {e}", exc_info=True)
            return []

    # ===========================================================================
    # /manage-picks seed
    # ===========================================================================

    @app_commands.command(
        name="manage-picks",
        description="[ADMIN] Manage draft picks for NBL"
    )
    @app_commands.describe(
        action="Action: seed, reassign, view, or by-team"
    )
    async def manage_picks(self, interaction: discord.Interaction, action: str):
        """Root command for pick management - delegates to subcommands."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "❌ This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        # Check permissions
        if not await self._check_admin_permission(interaction, interaction.guild.id):
            return

        # Validate action
        valid_actions = {"seed", "reassign", "view", "by-team"}
        if action.lower() not in valid_actions:
            await interaction.followup.send(
                f"❌ Invalid action. Valid options: {', '.join(valid_actions)}",
                ephemeral=True,
            )
            return

        # Route to subcommand
        if action.lower() == "seed":
            await self._seed_picks(interaction)
        elif action.lower() == "reassign":
            await self._reassign_picks(interaction)
        elif action.lower() == "view":
            await self._view_picks(interaction)
        elif action.lower() == "by-team":
            await self._view_by_team(interaction)

    async def _seed_picks(self, interaction: discord.Interaction) -> None:
        """Seed initial draft picks for NBL."""
        supabase = get_supabase()

        # Get NBL division
        nbl_div = await self._get_nbl_division(supabase)
        if not nbl_div:
            await interaction.followup.send(
                "❌ NBL division not found in database.",
                ephemeral=True,
            )
            logger.error("NBL division lookup failed in seed_picks")
            return

        # Get active season
        season = await self._get_active_nbl_season(supabase, nbl_div["id"])
        if not season:
            await interaction.followup.send(
                "❌ No active NBL season found.",
                ephemeral=True,
            )
            return

        # Get all NBL teams
        teams = await self._get_all_nbl_teams(supabase, nbl_div["id"])
        if not teams:
            await interaction.followup.send(
                "❌ No teams found in NBL division.",
                ephemeral=True,
            )
            return

        # Check if picks already exist for this season
        try:
            existing = supabase.table("draft_picks").select("id").eq(
                "season_id", season["id"]
            ).limit(1).execute()
            if existing.data:
                await interaction.followup.send(
                    "⚠️ Draft picks already seeded for this season. Use reassign to modify.",
                    ephemeral=True,
                )
                return
        except Exception as e:
            logger.error(f"Failed to check existing picks: {e}")
            await interaction.followup.send(
                "❌ Database error checking existing picks.",
                ephemeral=True,
            )
            return

        # Create picks
        try:
            repo = TransactionsRepository(supabase)
            
            # Default: 1 round, all teams
            num_rounds = 1
            picks_per_round = len(teams)
            overall_pick = 1

            picks_to_create = []
            for round_num in range(1, num_rounds + 1):
                for team_idx, team in enumerate(teams):
                    picks_to_create.append({
                        "season_id": season["id"],
                        "round": round_num,
                        "overall": overall_pick,
                        "position_in_round": team_idx + 1,
                        "original_team_id": team["id"],
                        "current_owner_team_id": team["id"],
                    })
                    overall_pick += 1

            # Insert all picks
            if picks_to_create:
                response = supabase.table("draft_picks").insert(picks_to_create).execute()
                if response.data:
                    logger.info(
                        f"🌱 Seeded {len(response.data)} draft picks for NBL S{season.get('name', '?')} "
                        f"({num_rounds} round(s), {picks_per_round} teams)"
                    )
                    await interaction.followup.send(
                        f"✅ Seeded {len(response.data)} draft picks for NBL!\n"
                        f"Rounds: {num_rounds}, Teams: {picks_per_round}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ Failed to insert picks into database.",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.followup.send(
                    "❌ No picks generated.",
                    ephemeral=True,
                )
                return

        except Exception as e:
            logger.error(f"Failed to seed picks: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Database error seeding picks.",
                ephemeral=True,
            )

    async def _reassign_picks(self, interaction: discord.Interaction) -> None:
        """Reassign a pick from one team to another."""
        supabase = get_supabase()

        # Get NBL division
        nbl_div = await self._get_nbl_division(supabase)
        if not nbl_div:
            await interaction.followup.send(
                "❌ NBL division not found.",
                ephemeral=True,
            )
            return

        # Get active season
        season = await self._get_active_nbl_season(supabase, nbl_div["id"])
        if not season:
            await interaction.followup.send(
                "❌ No active NBL season.",
                ephemeral=True,
            )
            return

        # Get all picks for this season
        try:
            picks_response = supabase.table("draft_picks").select(
                "*, current_owner_team:current_owner_team_id(code, name)"
            ).eq("season_id", season["id"]).order(
                "overall", desc=False
            ).execute()
            picks = picks_response.data or []

            if not picks:
                await interaction.followup.send(
                    "⚠️ No draft picks found for this season.",
                    ephemeral=True,
                )
                return
        except Exception as e:
            logger.error(f"Failed to fetch picks: {e}")
            await interaction.followup.send(
                "❌ Failed to fetch draft picks.",
                ephemeral=True,
            )
            return

        # Get all teams for selection
        teams = await self._get_all_nbl_teams(supabase, nbl_div["id"])
        if not teams:
            await interaction.followup.send(
                "❌ No teams found in NBL.",
                ephemeral=True,
            )
            return

        # For this implementation, just show status and instructions
        # A full implementation would use modals/selects
        embed = discord.Embed(
            title="🔄 Reassign Draft Pick",
            description="Pick reassignment requires multi-step interaction (modal + select).",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Total Picks Available",
            value=f"{len(picks)} picks",
            inline=False
        )
        embed.add_field(
            name="To Reassign",
            value="Use the admin dashboard: `/admin-transactions` → 🏆 Draft Picks → Reassign Pick",
            inline=False
        )
        embed.set_footer(text="This feature is available in the admin dashboard")

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _view_picks(self, interaction: discord.Interaction) -> None:
        """View all picks for active NBL season."""
        supabase = get_supabase()

        # Get NBL division
        nbl_div = await self._get_nbl_division(supabase)
        if not nbl_div:
            await interaction.followup.send(
                "❌ NBL division not found.",
                ephemeral=True,
            )
            return

        # Get active season
        season = await self._get_active_nbl_season(supabase, nbl_div["id"])
        if not season:
            await interaction.followup.send(
                "❌ No active NBL season.",
                ephemeral=True,
            )
            return

        # Get all picks for this season
        try:
            picks_response = supabase.table("draft_picks").select(
                "*, original_team:original_team_id(code, name), current_owner_team:current_owner_team_id(code, name)"
            ).eq("season_id", season["id"]).order(
                "overall", desc=False
            ).execute()
            picks = picks_response.data or []

            if not picks:
                await interaction.followup.send(
                    f"⚠️ No draft picks found for {season.get('name', 'active')} season.",
                    ephemeral=True,
                )
                return

            # Build embeds (max 10 picks per embed)
            embeds = []
            for i in range(0, len(picks), 10):
                chunk = picks[i:i+10]
                embed = discord.Embed(
                    title=f"📋 NBL Draft Picks - {season.get('name', 'Season')}",
                    description=f"Showing picks {i+1}-{min(i+10, len(picks))} of {len(picks)}",
                    color=discord.Color.purple(),
                )

                for pick in chunk:
                    original_team = pick.get("original_team", {}) or {}
                    current_owner = pick.get("current_owner_team", {}) or {}
                    original_code = original_team.get("code", "?")
                    owner_code = current_owner.get("code", "?")

                    pick_label = f"R{pick.get('round', '?')}P{pick.get('overall', '?')}"
                    value = f"Owner: {owner_code}"
                    if original_code != owner_code:
                        value += f" (Original: {original_code})"

                    embed.add_field(
                        name=f"#{pick.get('overall', '?')} - {pick_label}",
                        value=value,
                        inline=False
                    )

                embeds.append(embed)

            # Send embeds
            for embed in embeds[:10]:  # Discord limit
                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to fetch picks: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Database error fetching picks.",
                ephemeral=True,
            )

    async def _view_by_team(self, interaction: discord.Interaction) -> None:
        """View picks owned by a specific team."""
        supabase = get_supabase()

        # Get NBL division
        nbl_div = await self._get_nbl_division(supabase)
        if not nbl_div:
            await interaction.followup.send(
                "❌ NBL division not found.",
                ephemeral=True,
            )
            return

        # Get active season
        season = await self._get_active_nbl_season(supabase, nbl_div["id"])
        if not season:
            await interaction.followup.send(
                "❌ No active NBL season.",
                ephemeral=True,
            )
            return

        # Get all teams
        teams = await self._get_all_nbl_teams(supabase, nbl_div["id"])
        if not teams:
            await interaction.followup.send(
                "❌ No teams found in NBL.",
                ephemeral=True,
            )
            return

        # Build list of teams for display
        team_list = "\n".join([f"• {t['code']}: {t['name']}" for t in teams])

        embed = discord.Embed(
            title="🏀 View Team Picks",
            description=f"Pick a team from:\n{team_list}",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Usage",
            value="Use the admin dashboard: `/admin-transactions` → 🏆 Draft Picks → View by Team",
            inline=False
        )
        embed.set_footer(text="This feature is available in the admin dashboard")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Load the ManagePicksCommands cog."""
    await bot.add_cog(ManagePicksCommands(bot))
    logger.info("✅ ManagePicksCommands cog loaded")
