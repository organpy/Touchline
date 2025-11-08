"""UI components for the transactions workflow."""

from __future__ import annotations

import discord
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from cogs.score.roster_cache import RosterCache

if TYPE_CHECKING:  # pragma: no cover - hints only
    from .transactions import TransactionsCog


class PendingSigningView(discord.ui.View):
    """Buttons for a player to accept or decline a signing offer."""

    def __init__(
        self,
        cog: "TransactionsCog",
        pending_signing: Dict[str, Any],
        player: discord.Member,
        team: Dict[str, Any],
        season: Dict[str, Any]
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.pending_signing = pending_signing
        self.player = player
        self.team = team
        self.season = season
        self.resolved = False
        self.message: Optional[discord.Message] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def set_message(self, message: discord.Message) -> None:
        self.message = message

    def refresh_pending(self, pending: Dict[str, Any]) -> None:
        self.pending_signing = pending

    async def _finalize(self, embed: discord.Embed) -> None:
        self.resolved = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            await self.message.edit(embed=embed, view=self)

    async def _already_resolved(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "⚠️ This signing offer has already been resolved.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Discord view overrides
    # ------------------------------------------------------------------
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "❌ Only the targeted player can respond to this signing offer.",
                ephemeral=True,
            )
            return False

        if self.resolved:
            await self._already_resolved(interaction)
            return False
        return True

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------
    @discord.ui.button(label="Accept Offer", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await self._already_resolved(interaction)
            return

        await interaction.response.defer(ephemeral=True)

        # Ensure offer not expired
        expires_at = self.pending_signing.get('expires_at')
        if expires_at:
            try:
                expiry_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                current = datetime.now(expiry_dt.tzinfo)
                if expiry_dt < current:
                    await interaction.followup.send("⚠️ This offer has already expired.", ephemeral=True)
                    return
            except ValueError:
                pass

        # Prevent duplicate roster entries
        roster_entry = self.cog.repo.get_active_roster_entry(
            self.pending_signing['season_id'],
            self.pending_signing['player_id']
        )
        if roster_entry and roster_entry.get('team_id') == self.pending_signing['team_id']:
            await interaction.followup.send(
                "ℹ️ You are already listed on this roster. No action taken.",
                ephemeral=True,
            )
            self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'accepted')
            await self._finalize(self._already_signed_embed())
            return
        if roster_entry and roster_entry.get('team_id') != self.pending_signing['team_id']:
            await interaction.followup.send(
                "❌ You are currently rostered on another team. Please request a release first.",
                ephemeral=True,
            )
            self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'declined')
            await self._finalize(self._decline_embed(interaction.user))
            return

        # Insert transaction row (trigger will update roster)
        transaction = self.cog.repo.insert_transaction({
            'season_id': self.pending_signing['season_id'],
            'type': 'sign',
            'player_id': self.pending_signing['player_id'],
            'from_team_id': None,
            'to_team_id': self.pending_signing['team_id'],
            'executed_by': interaction.user.id,
            'details': {
                'accepted_at': datetime.utcnow().isoformat() + 'Z',
                'initiated_by': self.pending_signing.get('created_by'),
                'guild_id': self.pending_signing.get('guild_id')
            }
        })
        if not transaction:
            await interaction.followup.send("❌ Failed to record transaction. Please contact staff.", ephemeral=True)
            return

        self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'accepted')
        self.cog.repo.cancel_other_pending_signings(self.pending_signing['player_id'], keep_id=self.pending_signing['id'])

        # Refresh roster cache so new signing appears immediately
        RosterCache().invalidate()

        embed = discord.Embed(
            title="✅ Signing Accepted",
            description=f"{self.player.mention} has joined **{self.team['name']}**!",
            color=discord.Color.green()
        )
        embed.add_field(name="Player", value=str(self.player))
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)")
        embed.add_field(name="Accepted By", value=interaction.user.mention, inline=False)

        await self._finalize(embed)
        await interaction.followup.send("✅ Offer accepted! Welcome to the team.", ephemeral=True)

    @discord.ui.button(label="Decline Offer", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await self._already_resolved(interaction)
            return

        await interaction.response.defer(ephemeral=True)
        self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'declined')

        embed = self._decline_embed(interaction.user)
        await self._finalize(embed)
        await interaction.followup.send("🗂️ Offer declined.", ephemeral=True)

    # ------------------------------------------------------------------
    # Embed helpers
    # ------------------------------------------------------------------
    def _decline_embed(self, actor: discord.abc.User) -> discord.Embed:
        embed = discord.Embed(
            title="Signing Declined",
            description=f"{self.player.mention} declined the offer from **{self.team['name']}**.",
            color=discord.Color.red()
        )
        embed.add_field(name="Player", value=str(self.player))
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)")
        embed.add_field(name="Updated By", value=actor.mention, inline=False)
        return embed

    def _already_signed_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="No Changes Needed",
            description=f"{self.player.mention} is already rostered on **{self.team['name']}**.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Player", value=str(self.player))
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)")
        return embed
