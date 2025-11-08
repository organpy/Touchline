"""Trade proposal UI components."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord

from cogs.score.roster_cache import RosterCache
from db import queries

if TYPE_CHECKING:  # pragma: no cover - hints only
    from .transactions import TransactionsCog
    from .trade_approval import TradeApprovalView

logger = logging.getLogger(__name__)


class TradeAssetSelect(discord.ui.Select):
    """Dropdown to select players or picks for a trade."""

    def __init__(
        self,
        available_assets: List[Dict[str, Any]],
        asset_type: str,
        direction: str,
        max_selections: int = 10
    ):
        """
        Args:
            available_assets: List of dicts with 'id', 'label', 'description' keys.
            asset_type: 'player' or 'pick'.
            direction: 'outbound' or 'inbound'.
            max_selections: Max number of items to select.
        """
        self.asset_type = asset_type
        self.direction = direction
        self.selected_assets: List[Dict[str, Any]] = []

        options = [
            discord.SelectOption(
                label=asset.get('label', 'Unknown'),
                value=asset['id'],
                description=asset.get('description', '')[:100]
            )
            for asset in available_assets[:25]  # Discord limit
        ]

        super().__init__(
            placeholder=f"Select {asset_type}s to {direction}...",
            min_values=0,
            max_values=min(len(options), max_selections),
            options=options
        )
        self.available_assets = {asset['id']: asset for asset in available_assets}

    async def callback(self, interaction: discord.Interaction) -> None:
        self.selected_assets = [self.available_assets[val] for val in self.values]
        await interaction.response.defer()


class TradeAssetModal(discord.ui.Modal):
    """Modal for building a trade proposal with multi-asset selection."""

    def __init__(
        self,
        cog: "TransactionsCog",
        initiating_team: Dict[str, Any],
        counterparty_team: Dict[str, Any],
        season: Dict[str, Any],
        supabase
    ):
        super().__init__(title="Propose Trade")
        self.cog = cog
        self.initiating_team = initiating_team
        self.counterparty_team = counterparty_team
        self.season = season
        self.supabase = supabase

        self.notes = discord.ui.TextInput(
            label="Trade Notes (optional)",
            placeholder="e.g., 'Depth at PG'",
            required=False,
            max_length=200,
            style=discord.TextStyle.short
        )
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Fetch available rosters and picks
        initiating_rosters = await queries.get_active_rosters(self.supabase, self.season['id'])
        initiating_players = [
            r for r in initiating_rosters
            if r.get('team_id') == self.initiating_team['id']
        ]

        counterparty_rosters = await queries.get_active_rosters(self.supabase, self.season['id'])
        counterparty_players = [
            r for r in counterparty_rosters
            if r.get('team_id') == self.counterparty_team['id']
        ]

        initiating_picks = self.cog.repo.list_draft_picks(self.season['id'])
        initiating_picks = [
            p for p in initiating_picks
            if p.get('current_owner_team_id') == self.initiating_team['id']
        ]

        counterparty_picks = self.cog.repo.list_draft_picks(self.season['id'])
        counterparty_picks = [
            p for p in counterparty_picks
            if p.get('current_owner_team_id') == self.counterparty_team['id']
        ]

        # Build asset lists
        initiating_player_assets = [
            {
                'id': f"player_{r['player_id']}",
                'label': r['players']['display_name'],
                'description': 'Player',
                'type': 'player',
                'player_id': r['player_id']
            }
            for r in initiating_players
        ]

        initiating_pick_assets = [
            {
                'id': f"pick_{p['id']}",
                'label': f"R{p['round']}P{p['overall']:02d}",
                'description': 'Draft Pick',
                'type': 'pick',
                'pick_id': p['id']
            }
            for p in initiating_picks
        ]

        counterparty_player_assets = [
            {
                'id': f"player_{r['player_id']}",
                'label': r['players']['display_name'],
                'description': 'Player',
                'type': 'player',
                'player_id': r['player_id']
            }
            for r in counterparty_players
        ]

        counterparty_pick_assets = [
            {
                'id': f"pick_{p['id']}",
                'label': f"R{p['round']}P{p['overall']:02d}",
                'description': 'Draft Pick',
                'type': 'pick',
                'pick_id': p['id']
            }
            for p in counterparty_picks
        ]

        # Create selection view
        view = TradeAssetSelectionView(
            cog=self.cog,
            initiating_team=self.initiating_team,
            counterparty_team=self.counterparty_team,
            season=self.season,
            initiating_player_assets=initiating_player_assets,
            initiating_pick_assets=initiating_pick_assets,
            counterparty_player_assets=counterparty_player_assets,
            counterparty_pick_assets=counterparty_pick_assets,
            notes=self.notes.value or None,
            initiator_discord_id=interaction.user.id
        )

        embed = discord.Embed(
            title="Trade Proposal Builder",
            description=f"**{self.initiating_team['name']}** ↔ **{self.counterparty_team['name']}**",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Select Assets", value="Choose players and/or picks from each team below.")
        if self.notes.value:
            embed.add_field(name="Notes", value=self.notes.value, inline=False)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class TradeAssetSelectionView(discord.ui.View):
    """View for selecting trade assets."""

    def __init__(
        self,
        cog: "TransactionsCog",
        initiating_team: Dict[str, Any],
        counterparty_team: Dict[str, Any],
        season: Dict[str, Any],
        initiating_player_assets: List[Dict[str, Any]],
        initiating_pick_assets: List[Dict[str, Any]],
        counterparty_player_assets: List[Dict[str, Any]],
        counterparty_pick_assets: List[Dict[str, Any]],
        notes: Optional[str],
        initiator_discord_id: int
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.initiating_team = initiating_team
        self.counterparty_team = counterparty_team
        self.season = season
        self.notes = notes
        self.initiator_discord_id = initiator_discord_id

        if initiating_player_assets:
            self.add_item(TradeAssetSelect(
                initiating_player_assets,
                'player',
                'outbound',
                max_selections=10
            ))
        if initiating_pick_assets:
            self.add_item(TradeAssetSelect(
                initiating_pick_assets,
                'pick',
                'outbound',
                max_selections=10
            ))
        if counterparty_player_assets:
            self.add_item(TradeAssetSelect(
                counterparty_player_assets,
                'player',
                'inbound',
                max_selections=10
            ))
        if counterparty_pick_assets:
            self.add_item(TradeAssetSelect(
                counterparty_pick_assets,
                'pick',
                'inbound',
                max_selections=10
            ))

    def get_selected_assets(self) -> Dict[str, List[Dict[str, Any]]]:
        """Collect all selected assets by direction."""
        assets = {'outbound': [], 'inbound': []}
        for select in self.children:
            if isinstance(select, TradeAssetSelect):
                assets[select.direction].extend(select.selected_assets)
        return assets

    @discord.ui.button(label="Submit Trade Proposal", style=discord.ButtonStyle.success)
    async def submit_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.initiator_discord_id:
            await interaction.response.send_message(
                "❌ Only the initiator can submit this trade.",
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

        # Create trade proposal
        proposal = self.cog.repo.create_trade_proposal({
            'guild_id': interaction.guild_id,
            'season_id': self.season['id'],
            'initiating_team_id': self.initiating_team['id'],
            'counterparty_team_id': self.counterparty_team['id'],
            'status': 'pending',
            'created_by': interaction.user.id,
            'metadata': {
                'notes': self.notes,
                'initiator_discord_id': self.initiator_discord_id
            }
        })

        if not proposal:
            await interaction.followup.send(
                "❌ Failed to create trade proposal.",
                ephemeral=True,
            )
            return

        # Attach trade assets
        trade_assets = []
        for asset in assets['outbound']:
            trade_assets.append({
                'proposal_id': proposal['id'],
                'direction': 'outbound',
                'asset_type': asset.get('type', 'unknown'),
                'player_id': asset.get('player_id'),
                'draft_pick_id': asset.get('pick_id'),
                'metadata': {}
            })
        for asset in assets['inbound']:
            trade_assets.append({
                'proposal_id': proposal['id'],
                'direction': 'inbound',
                'asset_type': asset.get('type', 'unknown'),
                'player_id': asset.get('player_id'),
                'draft_pick_id': asset.get('pick_id'),
                'metadata': {}
            })

        self.cog.repo.attach_trade_assets(trade_assets)

        # Now send approval message to counterparty
        from .trade_approval import TradeApprovalView

        approval_view = TradeApprovalView(
            cog=self.cog,
            trade_proposal=proposal,
            initiating_team=self.initiating_team,
            counterparty_team=self.counterparty_team,
            trade_assets=trade_assets,
            season=self.season
        )

        approval_embed = self._build_approval_request_embed()

        cfg = await self.cog.config.get_transactions_config(interaction.guild_id)
        channel = interaction.channel
        channel_id = cfg.get('channel_id')
        if channel_id:
            configured_channel = interaction.guild.get_channel(channel_id)
            if isinstance(configured_channel, discord.TextChannel):
                channel = configured_channel

        if channel is None:
            self.cog.repo.update_trade_status(proposal['id'], 'cancelled')
            await interaction.followup.send(
                "❌ Unable to locate a channel to post the trade approval request.",
                ephemeral=True,
            )
            return

        try:
            counterparty_owner_id = self.counterparty_team.get('owner_discord_id')
            counterparty_mention = f"<@{counterparty_owner_id}>" if counterparty_owner_id else "Team"

            message = await channel.send(
                content=f"{counterparty_mention} you have a trade proposal to review!",
                embed=approval_embed,
                view=approval_view,
                allowed_mentions=discord.AllowedMentions(users=[]) if counterparty_owner_id else None
            )
            approval_view.set_message(message)
        except discord.Forbidden:
            self.cog.repo.update_trade_status(proposal['id'], 'cancelled')
            await interaction.followup.send(
                "❌ I don't have permission to post the trade approval in the configured channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.error("Failed to send trade approval message: %s", exc)
            self.cog.repo.update_trade_status(proposal['id'], 'cancelled')
            await interaction.followup.send(
                "❌ Something went wrong delivering the approval message.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Trade proposal created and sent to {self.counterparty_team['name']}!",
            ephemeral=True,
        )

        logger.info(
            "📋 Trade proposal %s: %s ↔ %s (creator=%s)",
            proposal['id'],
            self.initiating_team['code'],
            self.counterparty_team['code'],
            interaction.user
        )

    def _build_approval_request_embed(self) -> discord.Embed:
        """Build the trade proposal embed for counterparty approval."""
        embed = discord.Embed(
            title="Trade Proposal Review",
            description=f"**{self.initiating_team['name']}** wants to trade with you!",
            color=discord.Color.blurple()
        )

        assets = {'outbound': [], 'inbound': []}
        for select in self.children:
            if isinstance(select, TradeAssetSelect):
                assets[select.direction].extend(select.selected_assets)

        if assets['outbound']:
            lines = []
            for asset in assets['outbound']:
                if asset.get('type') == 'player':
                    lines.append(f"🧑 {asset.get('label', 'Unknown')}")
                elif asset.get('type') == 'pick':
                    lines.append(f"📋 {asset.get('label', 'Unknown')}")
            if lines:
                embed.add_field(
                    name=f"{self.initiating_team['name']} Sends",
                    value="\n".join(lines),
                    inline=True
                )

        if assets['inbound']:
            lines = []
            for asset in assets['inbound']:
                if asset.get('type') == 'player':
                    lines.append(f"🧑 {asset.get('label', 'Unknown')}")
                elif asset.get('type') == 'pick':
                    lines.append(f"📋 {asset.get('label', 'Unknown')}")
            if lines:
                embed.add_field(
                    name=f"{self.counterparty_team['name']} Sends",
                    value="\n".join(lines),
                    inline=True
                )

        embed.set_footer(text="Accept or Decline below")
        return embed

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.initiator_discord_id:
            await interaction.response.send_message(
                "❌ Only the initiator can cancel this trade.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        self.stop()
        await interaction.followup.send("Cancelled.", ephemeral=True)
