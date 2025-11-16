"""UI components for the transactions workflow."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from cogs.score.roster_cache import RosterCache
from utils.team_player_cache import TeamPlayerCache
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)

if TYPE_CHECKING:  # pragma: no cover - hints only
    from .transactions import TransactionsCog


logger = logging.getLogger(__name__)


class PendingSigningView(discord.ui.View):
    """Buttons for a player to accept or decline a signing offer."""

    def __init__(
        self,
        cog: "TransactionsCog",
        pending_signing: Dict[str, Any],
        player: discord.Member,
        team: Dict[str, Any],
        season: Dict[str, Any],
        league_display: str,
        league_code: str,
        team_logo_filename: Optional[str] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.pending_signing = pending_signing
        self.player = player
        self.team = team
        self.season = season
        self.league_display = league_display
        self.league_code = league_code
        self.team_logo_filename = team_logo_filename
        self.season_name = self.season.get('name', 'Unknown Season')
        self.resolved = False
        self.message: Optional[discord.Message] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def set_message(self, message: discord.Message) -> None:
        self.message = message

    def refresh_pending(self, pending: Dict[str, Any]) -> None:
        self.pending_signing = pending

    def _decorate_embed(self, embed: discord.Embed) -> None:
        if self.team_logo_filename:
            target_thumbnail(embed, self.team_logo_filename)
        apply_team_badge(embed, self.team.get('code'))
        apply_league_badge(embed, self.league_code)

    async def _finalize(self, embed: discord.Embed) -> None:
        self._decorate_embed(embed)
        self.resolved = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            attachments = list(self.message.attachments) if self.message.attachments else None
            await self.message.edit(embed=embed, view=self, attachments=attachments)

    async def _already_resolved(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "⚠️ This signing offer has already been resolved.",
            ephemeral=True,
        )

    def _league_field_value(self) -> str:
        code = (self.league_code or self.season.get('league_type') or '').upper()
        if self.league_display and code and self.league_display.upper() != code:
            return self.league_display
        if self.league_display:
            return self.league_display
        return code or "Unknown League"

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

        logger.info(
            "🔍 Player %s accepting signing offer for %s (pending_id=%s)",
            interaction.user.id,
            self.team['code'],
            self.pending_signing['id']
        )

        # Ensure offer not expired
        expires_at = self.pending_signing.get('expires_at')
        if expires_at:
            try:
                expiry_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                current = datetime.now(expiry_dt.tzinfo)
                if expiry_dt < current:
                    logger.warning("⏰ Signing offer %s expired", self.pending_signing['id'])
                    await interaction.followup.send("⚠️ This offer has already expired.", ephemeral=True)
                    return
            except ValueError:
                logger.error("⚠️ Could not parse expiry date: %s", expires_at)

        # Prevent duplicate roster entries
        roster_entry = self.cog.repo.get_active_roster_entry(
            self.pending_signing['season_id'],
            self.pending_signing['player_id']
        )
        if roster_entry and roster_entry.get('team_id') == self.pending_signing['team_id']:
            logger.info(
                "ℹ️ Player %s already on roster for %s, marking signing as accepted",
                self.pending_signing['player_id'],
                self.team['code']
            )
            await interaction.followup.send(
                "ℹ️ You are already listed on this roster. No action taken.",
                ephemeral=True,
            )
            self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'accepted')
            await self._finalize(self._already_signed_embed())
            return
        if roster_entry and roster_entry.get('team_id') != self.pending_signing['team_id']:
            logger.warning(
                "❌ Player %s cannot join %s, already on %s",
                self.pending_signing['player_id'],
                self.team['code'],
                roster_entry.get('team_id')
            )
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
            logger.error("❌ Failed to insert transaction for signing %s", self.pending_signing['id'])
            await interaction.followup.send("❌ Failed to record transaction. Please contact staff.", ephemeral=True)
            return

        logger.info(
            "✅ Transaction recorded: player %s signed to %s (signing_id=%s, txn_id=%s)",
            self.pending_signing['player_id'],
            self.team['code'],
            self.pending_signing['id'],
            transaction['id']
        )

        self.cog.repo.update_pending_signing_status(self.pending_signing['id'], 'accepted')
        self.cog.repo.cancel_other_pending_signings(self.pending_signing['player_id'], keep_id=self.pending_signing['id'])

        # Refresh roster cache so new signing appears immediately
        logger.info("🔄 Invalidating roster cache for signing acceptance")
        RosterCache().invalidate()
        logger.info("🔄 Invalidating team player cache for signing acceptance")
        await TeamPlayerCache.refresh()

        embed = discord.Embed(
            title="✅ Signing Accepted",
            description=f"{self.player.mention} has joined **{self.team['name']}**!",
            color=discord.Color.green()
        )
        embed.add_field(name="League", value=self._league_field_value(), inline=True)
        embed.add_field(name="Season", value=self.season_name, inline=True)
        embed.add_field(name="Player", value=str(self.player), inline=True)
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        embed.add_field(name="Accepted By", value=interaction.user.mention, inline=True)
        embed.timestamp = datetime.utcnow()

        await self._finalize(embed)
        await interaction.followup.send("✅ Offer accepted! Welcome to the team.", ephemeral=True)

    @discord.ui.button(label="Decline Offer", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await self._already_resolved(interaction)
            return

        await interaction.response.defer(ephemeral=True)
        logger.info(
            "🗂️ Player %s declining signing offer for %s",
            interaction.user.id,
            self.team['code']
        )
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
        embed.add_field(name="League", value=self._league_field_value(), inline=True)
        embed.add_field(name="Season", value=self.season_name, inline=True)
        embed.add_field(name="Player", value=str(self.player), inline=True)
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        embed.add_field(name="Updated By", value=actor.mention, inline=True)
        embed.timestamp = datetime.utcnow()
        return embed

    def _already_signed_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="No Changes Needed",
            description=f"{self.player.mention} is already rostered on **{self.team['name']}**.",
            color=discord.Color.gold()
        )
        embed.add_field(name="League", value=self._league_field_value(), inline=True)
        embed.add_field(name="Season", value=self.season_name, inline=True)
        embed.add_field(name="Player", value=str(self.player), inline=True)
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        embed.timestamp = datetime.utcnow()
        return embed


class ReleaseConfirmationModal(discord.ui.Modal):
    """Modal to confirm releasing a player from the roster."""

    def __init__(
        self,
        cog: "TransactionsCog",
        player_member: discord.Member,
        player_record: Dict[str, Any],
        team: Dict[str, Any],
        season: Dict[str, Any],
        channel_id: Optional[int],
        league_display: str,
        league_code: str,
    ):
        super().__init__(title="Confirm Release")
        self.cog = cog
        self.player_member = player_member
        self.player_record = player_record
        self.team = team
        self.season = season
        self.channel_id = channel_id
        self.league_display = league_display
        self.league_code = league_code
        self.season_name = self.season.get('name', 'Unknown Season')

        self.reason: discord.ui.TextInput = discord.ui.TextInput(
            label="Reason (optional)",
            placeholder="Provide context for this release",
            required=False,
            max_length=200,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        roster_entry = self.cog.repo.get_active_roster_entry(self.season['id'], self.player_record['id'])
        if not roster_entry or roster_entry.get('team_id') != self.team['id']:
            logger.warning(
                "❌ Release attempt failed: player %s not on roster for %s",
                self.player_record['id'],
                self.team['code']
            )
            await interaction.response.send_message(
                "❌ That player is no longer active on your roster.",
                ephemeral=True,
            )
            return

        reason = (self.reason.value or "").strip()
        logger.info(
            "📝 Processing release: player %s from %s (reason: %s)",
            self.player_record['id'],
            self.team['code'],
            reason or "none"
        )
        transaction = self.cog.repo.insert_transaction({
            'season_id': self.season['id'],
            'type': 'release',
            'player_id': self.player_record['id'],
            'from_team_id': self.team['id'],
            'to_team_id': None,
            'executed_by': interaction.user.id,
            'details': {
                'released_at': datetime.utcnow().isoformat() + 'Z',
                'initiated_by': interaction.user.id,
                'guild_id': interaction.guild_id,
                **({'reason': reason} if reason else {})
            }
        })
        if not transaction:
            logger.error("❌ Failed to insert transaction for release of %s", self.player_record['id'])
            await interaction.response.send_message(
                "❌ Failed to record release transaction.",
                ephemeral=True,
            )
            return

        logger.info(
            "✅ Transaction recorded: player %s released from %s (txn_id=%s)",
            self.player_record['id'],
            self.team['code'],
            transaction['id']
        )

        # Mark roster entry as left
        logger.info("📝 Marking roster entry as left for player %s", self.player_record['id'])
        if not self.cog.repo.mark_player_released(
            self.season['id'],
            self.player_record['id'],
            self.team['id']
        ):
            logger.warning("⚠️ Failed to mark roster entry as left for player %s", self.player_record['id'])

        logger.info("🔄 Invalidating roster cache for release")
        RosterCache().invalidate()
        logger.info("🔄 Invalidating team player cache for release")
        await TeamPlayerCache.refresh()

        embed = discord.Embed(
            title="Player Released",
            description=f"{self.player_member.mention} has been released from **{self.team['name']}**.",
            color=discord.Color.orange()
        )
        league_value = self.league_display or (self.league_code or self.season.get('league_type') or 'Unknown League')
        embed.add_field(name="League", value=league_value, inline=True)
        embed.add_field(name="Season", value=self.season_name, inline=True)
        embed.add_field(name="Player", value=str(self.player_member), inline=True)
        embed.add_field(name="Team", value=f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        embed.add_field(name="Released By", value=interaction.user.mention, inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()

        files, thumbnail = build_team_logo_payload(self.team.get('code'))
        if thumbnail:
            target_thumbnail(embed, thumbnail)
        apply_team_badge(embed, self.team.get('code'))
        apply_league_badge(embed, self.league_code)

        logger.info("🔄 Invalidating roster cache for release")

        announcement_error = False
        if self.channel_id:
            channel = interaction.guild.get_channel(self.channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(embed=embed, files=files or None)
                except discord.Forbidden:
                    logger.warning(
                        "Missing permissions to post release announcement in channel %s",
                        channel.id
                    )
                    announcement_error = True
                except discord.HTTPException as exc:
                    logger.error("HTTP error posting release announcement: %s", exc)
                    announcement_error = True
            else:
                announcement_error = True

        message = "✅ Player released."
        if announcement_error:
            message = (
                "✅ Player released, but I could not announce it in the configured channel."
            )

        await interaction.response.send_message(message, ephemeral=True)
