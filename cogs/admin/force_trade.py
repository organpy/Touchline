"""Admin force trade command - execute trades without confirmation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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


class ForceTradeAssetSelect(discord.ui.Select):
    """Dropdown to select players or picks for a force trade."""

    def __init__(
        self,
        available_assets: List[Dict[str, Any]],
        asset_type: str,
        direction: str,
        team_code: str,
        max_selections: int = 10
    ):
        self.asset_type = asset_type
        self.direction = direction
        self.selected_assets: List[Dict[str, Any]] = []

        options = [
            discord.SelectOption(
                label=asset.get('label', 'Unknown'),
                value=asset['id'],
                description=asset.get('description', '')[:100] if asset.get('description') else None
            )
            for asset in available_assets[:25]  # Discord limit
        ]

        placeholder = f"Select {asset_type}s from {team_code}..."

        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=min(len(options), max_selections),
            options=options
        )
        self.available_assets = {asset['id']: asset for asset in available_assets}

    async def callback(self, interaction: discord.Interaction) -> None:
        self.selected_assets = [self.available_assets[val] for val in self.values]
        await interaction.response.defer()


class ForceTradeAssetSelectionView(discord.ui.View):
    """View for selecting force trade assets with dropdowns."""

    def __init__(
        self,
        cog: "ForceTradeCommand",
        team1: Dict[str, Any],
        team2: Dict[str, Any],
        season: Dict[str, Any],
        supabase,
        team1_player_assets: List[Dict[str, Any]],
        team1_pick_assets: List[Dict[str, Any]],
        team2_player_assets: List[Dict[str, Any]],
        team2_pick_assets: List[Dict[str, Any]],
        initiator_discord_id: int
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.team1 = team1
        self.team2 = team2
        self.season = season
        self.supabase = supabase
        self.initiator_discord_id = initiator_discord_id

        # Add selects for team1 assets (going to team2)
        if team1_player_assets:
            self.add_item(ForceTradeAssetSelect(
                team1_player_assets,
                'player',
                'outbound',
                team1['code'],
                max_selections=10
            ))
        if team1_pick_assets:
            self.add_item(ForceTradeAssetSelect(
                team1_pick_assets,
                'pick',
                'outbound',
                team1['code'],
                max_selections=10
            ))

        # Add selects for team2 assets (going to team1)
        if team2_player_assets:
            self.add_item(ForceTradeAssetSelect(
                team2_player_assets,
                'player',
                'inbound',
                team2['code'],
                max_selections=10
            ))
        if team2_pick_assets:
            self.add_item(ForceTradeAssetSelect(
                team2_pick_assets,
                'pick',
                'inbound',
                team2['code'],
                max_selections=10
            ))

    def get_selected_assets(self) -> Dict[str, List[Dict[str, Any]]]:
        """Collect all selected assets by direction."""
        assets = {'outbound': [], 'inbound': []}
        for item in self.children:
            if isinstance(item, ForceTradeAssetSelect):
                assets[item.direction].extend(item.selected_assets)
        return assets

    @discord.ui.button(label="Execute Trade", style=discord.ButtonStyle.danger, row=4)
    async def execute_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Execute the force trade."""
        if interaction.user.id != self.initiator_discord_id:
            await interaction.response.send_message(
                "❌ Only the initiator can execute this trade.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        assets = self.get_selected_assets()
        if not assets['outbound'] and not assets['inbound']:
            await interaction.followup.send(
                "❌ Select at least one asset to trade.",
                ephemeral=True,
            )
            return

        try:
            repo = TransactionsRepository(self.supabase)

            # Create trade proposal directly as executed
            trade_payload = {
                "season_id": self.season["id"],
                "initiating_team_id": self.team1["id"],
                "counterparty_team_id": self.team2["id"],
                "created_by": interaction.user.id,
                "status": "executed",
            }

            trade = repo.create_trade_proposal(trade_payload)
            if not trade:
                await interaction.followup.send("❌ Failed to create trade.", ephemeral=True)
                return

            trade_id = trade['id'] if isinstance(trade, dict) else trade

            # Attach trade assets
            trade_assets = []
            for asset in assets['outbound']:
                trade_assets.append({
                    'proposal_id': trade_id,
                    'direction': 'outbound',
                    'asset_type': asset.get('type', 'player'),
                    'player_id': asset.get('player_id'),
                    'draft_pick_id': asset.get('draft_pick_id'),
                    'metadata': {}
                })
            for asset in assets['inbound']:
                trade_assets.append({
                    'proposal_id': trade_id,
                    'direction': 'inbound',
                    'asset_type': asset.get('type', 'player'),
                    'player_id': asset.get('player_id'),
                    'draft_pick_id': asset.get('draft_pick_id'),
                    'metadata': {}
                })

            repo.attach_trade_assets(trade_assets)

            # Refresh cache
            await TeamPlayerCache.refresh(self.supabase)

            # Build confirmation embed
            embed = discord.Embed(
                title="Trade Executed",
                description=f"**{self.team1['name']}** ↔ **{self.team2['name']}**",
                color=0x2B2D31,
            )

            # Team 1 gives
            outbound_lines = []
            for asset in assets['outbound']:
                if asset.get('type') == 'player':
                    outbound_lines.append(f"🧑 {asset.get('label', 'Unknown')}")
                elif asset.get('type') == 'pick':
                    outbound_lines.append(f"📋 {asset.get('label', 'Unknown')}")
            embed.add_field(
                name=f"{self.team1['code']} Sends",
                value="\n".join(outbound_lines) if outbound_lines else "Nothing",
                inline=True,
            )

            # Team 2 gives
            inbound_lines = []
            for asset in assets['inbound']:
                if asset.get('type') == 'player':
                    inbound_lines.append(f"🧑 {asset.get('label', 'Unknown')}")
                elif asset.get('type') == 'pick':
                    inbound_lines.append(f"📋 {asset.get('label', 'Unknown')}")
            embed.add_field(
                name=f"{self.team2['code']} Sends",
                value="\n".join(inbound_lines) if inbound_lines else "Nothing",
                inline=True,
            )

            embed.add_field(name="Executed By", value=interaction.user.mention, inline=False)
            trade_id_str = trade_id[:8] if isinstance(trade_id, str) else str(trade_id)[:8]
            embed.set_footer(text=f"Trade ID: {trade_id_str}")

            self.stop()
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"✅ Force trade executed: {self.team1['code']} <-> {self.team2['code']} by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error executing force trade: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=4)
    async def cancel_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel the force trade."""
        if interaction.user.id != self.initiator_discord_id:
            await interaction.response.send_message(
                "❌ Only the initiator can cancel this.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        self.stop()
        await interaction.followup.send("Cancelled.", ephemeral=True)


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
        await interaction.response.defer(ephemeral=True)

        supabase = get_supabase()
        if not supabase:
            await interaction.followup.send("❌ Database connection failed.", ephemeral=True)
            return

        # Get teams from cache (case-insensitive)
        cache = TeamPlayerCache()
        team1_record = cache.get_team_by_code(team1)
        team2_record = cache.get_team_by_code(team2)

        if not team1_record:
            await interaction.followup.send(f"❌ Team **{team1}** not found.", ephemeral=True)
            return

        if not team2_record:
            await interaction.followup.send(f"❌ Team **{team2}** not found.", ephemeral=True)
            return

        if team1_record["id"] == team2_record["id"]:
            await interaction.followup.send(
                "❌ Cannot trade a team with itself.",
                ephemeral=True,
            )
            return

        # Get active season
        season = await queries.get_active_season(supabase)
        if not season:
            await interaction.followup.send("❌ No active season configured.", ephemeral=True)
            return

        # Check if picks can be traded
        allow_picks = await can_trade_picks(
            team1_record['division_id'],
            team2_record['division_id'],
            supabase
        )

        # Fetch available assets for both teams
        team1_assets = await get_available_trade_assets(
            team1_record['id'],
            season['id'],
            allow_picks=allow_picks,
            supabase=supabase
        )
        team2_assets = await get_available_trade_assets(
            team2_record['id'],
            season['id'],
            allow_picks=allow_picks,
            supabase=supabase
        )

        # Check if there are any assets to trade
        has_assets = (
            team1_assets.get('players') or team1_assets.get('picks') or
            team2_assets.get('players') or team2_assets.get('picks')
        )
        if not has_assets:
            await interaction.followup.send(
                "❌ Neither team has any players or picks to trade.",
                ephemeral=True,
            )
            return

        # Create selection view
        view = ForceTradeAssetSelectionView(
            cog=self,
            team1=team1_record,
            team2=team2_record,
            season=season,
            supabase=supabase,
            team1_player_assets=team1_assets.get('players', []),
            team1_pick_assets=team1_assets.get('picks', []),
            team2_player_assets=team2_assets.get('players', []),
            team2_pick_assets=team2_assets.get('picks', []),
            initiator_discord_id=interaction.user.id
        )

        # Build embed
        embed = discord.Embed(
            title="Force Trade",
            description=f"**{team1_record['name']}** ↔ **{team2_record['name']}**\n\nSelect assets from each team below.",
            color=0x2B2D31,
        )

        if not allow_picks:
            embed.add_field(
                name="Note",
                value="Pick trading not allowed between these divisions.",
                inline=False
            )

        embed.set_footer(text="Select assets then click Execute Trade")

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ForceTradeCommand(bot))
    logger.info("✅ ForceTradeCommand cog loaded")
