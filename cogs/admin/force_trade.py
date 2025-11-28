"""Admin force trade command - execute trades without confirmation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.team_player_cache import TeamPlayerCache
from utils.embed_builder import EmbedBuilder
from utils.team_display import format_team_label_from_record
from utils.embed_assets import apply_league_badge, apply_team_badge, target_thumbnail
from utils.autocomplete_helper import AutocompleteHelper
from cogs.admin.common import LazyInitView
from cogs.transactions.trade_utils import can_trade_picks, get_available_trade_assets

if TYPE_CHECKING:  # pragma: no cover - hints only
    pass

logger = logging.getLogger(__name__)


async def autocomplete_team_code(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Wrapper for team autocomplete that includes interaction parameter."""
    return await AutocompleteHelper.team_code_choices(current)


class ForceTradeModal(discord.ui.Modal):
    """Modal for specifying trade assets when force-trading."""

    def __init__(self, cog: "ForceTradeCommand", team1: Dict[str, Any], team2: Dict[str, Any], allow_picks: bool = True):
        super().__init__(title="Force Trade Assets")
        self.cog = cog
        self.team1 = team1
        self.team2 = team2
        self.allow_picks = allow_picks

        pick_note = "\nLeave blank for none" if allow_picks else "\n(No picks allowed between divisions)"
        
        self.team1_assets = discord.ui.TextInput(
            label=f"Assets FROM {team1['code']} to {team2['code']}",
            placeholder="player_name, player_name, draft_pick_1" + pick_note,
            required=False,
            style=discord.TextStyle.paragraph,
        )
        self.team2_assets = discord.ui.TextInput(
            label=f"Assets FROM {team2['code']} to {team1['code']}",
            placeholder="player_name, player_name, draft_pick_1" + pick_note,
            required=False,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.team1_assets)
        self.add_item(self.team2_assets)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle trade asset submission."""
        await interaction.response.defer(ephemeral=True)

        supabase = get_supabase()
        if not supabase:
            await interaction.followup.send("❌ Database connection failed.", ephemeral=True)
            return

        season = await queries.get_active_season(supabase)
        if not season:
            await interaction.followup.send("❌ No active season configured.", ephemeral=True)
            return

        try:
            # Parse assets
            team1_assets_list = [a.strip() for a in self.team1_assets.value.split(",") if a.strip()] if self.team1_assets.value else []
            team2_assets_list = [a.strip() for a in self.team2_assets.value.split(",") if a.strip()] if self.team2_assets.value else []

            # Create trade proposal directly in database (bypassing confirmation)
            repo = TransactionsRepository(supabase)
            
            trade_payload = {
                "season_id": season["id"],
                "initiating_team_id": self.team1["id"],
                "counterparty_team_id": self.team2["id"],
                "created_by": interaction.user.id,
                "status": "executed",  # Directly mark as executed (bypasses pending state)
            }

            trade_id = repo.create_trade_proposal(trade_payload)
            if not trade_id:
                await interaction.followup.send("❌ Failed to create trade proposal.", ephemeral=True)
                return

            # Add trade assets
            for asset in team1_assets_list:
                asset_payload = {
                    "trade_proposal_id": trade_id,
                    "direction": "outbound",
                    "asset_type": "pick" if asset.startswith("draft") else "player",
                    "asset_name": asset,
                }
                repo.create_trade_asset(asset_payload)

            for asset in team2_assets_list:
                asset_payload = {
                    "trade_proposal_id": trade_id,
                    "direction": "inbound",
                    "asset_type": "pick" if asset.startswith("draft") else "player",
                    "asset_name": asset,
                }
                repo.create_trade_asset(asset_payload)

            # Refresh cache
            await TeamPlayerCache.refresh(supabase)

            # Build confirmation embed
            team1_label = format_team_label_from_record(self.team1)
            team2_label = format_team_label_from_record(self.team2)

            embed = discord.Embed(
                title="Trade Executed",
                description=f"Force trade between {team1_label} and {team2_label}",
                color=discord.Color.green(),
            )

            if team1_assets_list:
                embed.add_field(
                    name=f"{self.team1['code']} Gives",
                    value="\n".join(team1_assets_list),
                    inline=True,
                )
            else:
                embed.add_field(name=f"{self.team1['code']} Gives", value="Nothing", inline=True)

            if team2_assets_list:
                embed.add_field(
                    name=f"{self.team2['code']} Gives",
                    value="\n".join(team2_assets_list),
                    inline=True,
                )
            else:
                embed.add_field(name=f"{self.team2['code']} Gives", value="Nothing", inline=True)

            embed.add_field(name="Executed By", value=interaction.user.mention, inline=False)
            embed.set_footer(text=f"Trade ID: {trade_id[:8]}")

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"✅ Force trade executed: {self.team1['code']} <-> {self.team2['code']} by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error executing force trade: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)


class ForceTradeCommand(commands.Cog):
    """Admin force trade command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()

    @app_commands.command(
        name="force-trade",
        description="[ADMIN] Force execute a trade between two teams without confirmation",
    )
    @app_commands.describe(
        team1="First team in the trade",
        team2="Second team in the trade",
    )
    @app_commands.autocomplete(team1=autocomplete_team_code)
    @app_commands.autocomplete(team2=autocomplete_team_code)
    async def force_trade(
        self,
        interaction: discord.Interaction,
        team1: str,
        team2: str,
    ) -> None:
        """Allow admin to force a trade between two teams."""
        # NOTE: Do NOT defer here - modals must be sent as the first response
        
        supabase = get_supabase()
        if not supabase:
            await interaction.response.send_message("❌ Database connection failed.", ephemeral=True)
            return

        # Get teams from cache (case-insensitive)
        cache = TeamPlayerCache()
        team1_record = cache.get_team_by_code(team1)
        team2_record = cache.get_team_by_code(team2)

        if not team1_record:
            await interaction.response.send_message(f"❌ Team **{team1}** not found.", ephemeral=True)
            return

        if not team2_record:
            await interaction.response.send_message(f"❌ Team **{team2}** not found.", ephemeral=True)
            return

        if team1_record["id"] == team2_record["id"]:
            await interaction.response.send_message(
                "❌ Cannot trade a team with itself.",
                ephemeral=True,
            )
            return

        # Check if picks can be traded
        allow_picks = await can_trade_picks(
            team1_record['division_id'],
            team2_record['division_id'],
            supabase
        )

        # Show modal for asset selection (must be the first response)
        modal = ForceTradeModal(self, team1_record, team2_record, allow_picks=allow_picks)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ForceTradeCommand(bot))
    logger.info("✅ ForceTradeCommand cog loaded")
