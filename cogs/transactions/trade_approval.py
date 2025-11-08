"""Trade execution and approval flow."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from cogs.score.roster_cache import RosterCache

if TYPE_CHECKING:  # pragma: no cover - hints only
    from .transactions import TransactionsCog

logger = logging.getLogger(__name__)


class TradeApprovalView(discord.ui.View):
    """Buttons for counterparty to approve or decline a trade proposal."""

    def __init__(
        self,
        cog: "TransactionsCog",
        trade_proposal: Dict[str, Any],
        initiating_team: Dict[str, Any],
        counterparty_team: Dict[str, Any],
        trade_assets: list,
        season: Dict[str, Any]
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.trade_proposal = trade_proposal
        self.initiating_team = initiating_team
        self.counterparty_team = counterparty_team
        self.trade_assets = trade_assets
        self.season = season
        self.resolved = False
        self.message: Optional[discord.Message] = None

    def set_message(self, message: discord.Message) -> None:
        self.message = message

    async def _finalize(self, embed: discord.Embed) -> None:
        self.resolved = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            await self.message.edit(embed=embed, view=self)

    async def _already_resolved(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "⚠️ This trade proposal has already been resolved.",
            ephemeral=True,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only counterparty team owner can approve/decline
        counterparty_owner_id = self.counterparty_team.get('owner_discord_id')
        if interaction.user.id != counterparty_owner_id:
            await interaction.response.send_message(
                "❌ Only the counterparty team owner can respond to this trade.",
                ephemeral=True,
            )
            return False

        if self.resolved:
            await self._already_resolved(interaction)
            return False
        return True

    @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success)
    async def approve_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await self._already_resolved(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        # Execute the trade
        success = await self._execute_trade(interaction.user.id)
        if not success:
            await interaction.followup.send(
                "❌ Failed to execute trade. Check server logs.",
                ephemeral=True,
            )
            return

        embed = self._acceptance_embed()
        await self._finalize(embed)
        await interaction.followup.send("✅ Trade executed successfully!", ephemeral=True)

    @discord.ui.button(label="Decline Trade", style=discord.ButtonStyle.danger)
    async def decline_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await self._already_resolved(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        self.cog.repo.update_trade_status(self.trade_proposal['id'], 'declined')

        embed = self._decline_embed(interaction.user)
        await self._finalize(embed)
        await interaction.followup.send("Trade declined.", ephemeral=True)

    async def _execute_trade(self, executor_id: int) -> bool:
        """
        Execute the trade atomically:
        1. Update roster entries for players (mark left_at, insert new rosters)
        2. Update draft pick ownership
        3. Insert transaction records
        4. Refresh cache
        """
        try:
            outbound_assets = [a for a in self.trade_assets if a['direction'] == 'outbound']
            inbound_assets = [a for a in self.trade_assets if a['direction'] == 'inbound']

            # Process player transfers (outbound players go to counterparty)
            for asset in outbound_assets:
                if asset.get('asset_type') != 'player':
                    continue
                player_id = asset.get('player_id')
                if not player_id:
                    continue

                success = self.cog.repo.transfer_player_between_teams(
                    self.season['id'],
                    player_id,
                    self.initiating_team['id'],
                    self.counterparty_team['id']
                )
                if not success:
                    logger.error("Failed to transfer player %s", player_id)
                    return False

            # Process player transfers (inbound players come from counterparty)
            for asset in inbound_assets:
                if asset.get('asset_type') != 'player':
                    continue
                player_id = asset.get('player_id')
                if not player_id:
                    continue

                success = self.cog.repo.transfer_player_between_teams(
                    self.season['id'],
                    player_id,
                    self.counterparty_team['id'],
                    self.initiating_team['id']
                )
                if not success:
                    logger.error("Failed to transfer player %s", player_id)
                    return False

            # Update picks (outbound picks go to counterparty)
            for asset in outbound_assets:
                if asset.get('asset_type') != 'pick':
                    continue
                pick_id = asset.get('draft_pick_id')
                if pick_id:
                    self.cog.repo.update_draft_pick_owner(pick_id, self.counterparty_team['id'])

            # Update picks (inbound picks come from counterparty)
            for asset in inbound_assets:
                if asset.get('asset_type') != 'pick':
                    continue
                pick_id = asset.get('draft_pick_id')
                if pick_id:
                    self.cog.repo.update_draft_pick_owner(pick_id, self.initiating_team['id'])

            # Insert transaction record
            transaction = self.cog.repo.insert_transaction({
                'season_id': self.season['id'],
                'type': 'trade',
                'player_id': None,
                'from_team_id': self.initiating_team['id'],
                'to_team_id': self.counterparty_team['id'],
                'executed_by': executor_id,
                'details': {
                    'executed_at': datetime.utcnow().isoformat() + 'Z',
                    'trade_proposal_id': self.trade_proposal['id'],
                    'assets_outbound': len(outbound_assets),
                    'assets_inbound': len(inbound_assets)
                }
            })

            if not transaction:
                logger.error("Failed to insert transaction record for trade %s", self.trade_proposal['id'])
                return False

            # Update trade status to executed
            self.cog.repo.update_trade_status(self.trade_proposal['id'], 'executed')

            # Refresh cache
            RosterCache().invalidate()

            logger.info(
                "✅ Trade executed: %s → %s (proposal=%s)",
                self.initiating_team['code'],
                self.counterparty_team['code'],
                self.trade_proposal['id']
            )

            return True

        except Exception as exc:
            logger.error("Error executing trade %s: %s", self.trade_proposal['id'], exc, exc_info=True)
            return False

    def _acceptance_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Trade Executed",
            description=f"**{self.initiating_team['name']}** ↔ **{self.counterparty_team['name']}**",
            color=discord.Color.green()
        )

        outbound_assets = [a for a in self.trade_assets if a['direction'] == 'outbound']
        inbound_assets = [a for a in self.trade_assets if a['direction'] == 'inbound']

        if outbound_assets:
            lines = []
            for asset in outbound_assets:
                if asset['asset_type'] == 'player':
                    lines.append(f"🧑 {asset.get('label', 'Unknown Player')}")
                elif asset['asset_type'] == 'pick':
                    lines.append(f"📋 {asset.get('label', 'Unknown Pick')}")
            if lines:
                embed.add_field(
                    name=f"From {self.initiating_team['name']}",
                    value="\n".join(lines),
                    inline=False
                )

        if inbound_assets:
            lines = []
            for asset in inbound_assets:
                if asset['asset_type'] == 'player':
                    lines.append(f"🧑 {asset.get('label', 'Unknown Player')}")
                elif asset['asset_type'] == 'pick':
                    lines.append(f"📋 {asset.get('label', 'Unknown Pick')}")
            if lines:
                embed.add_field(
                    name=f"From {self.counterparty_team['name']}",
                    value="\n".join(lines),
                    inline=False
                )

        embed.timestamp = datetime.utcnow()
        return embed

    def _decline_embed(self, actor: discord.abc.User) -> discord.Embed:
        embed = discord.Embed(
            title="Trade Declined",
            description=f"**{self.counterparty_team['name']}** declined the trade proposal.",
            color=discord.Color.red()
        )
        embed.add_field(name="Initiator", value=self.initiating_team['name'])
        embed.add_field(name="Declined By", value=actor.mention, inline=False)
        embed.timestamp = datetime.utcnow()
        return embed
