"""UI components for the transactions workflow."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from cogs.score.roster_cache import RosterCache
from utils.team_player_cache import TeamPlayerCache
from utils.embed_builder import EmbedBuilder
from cogs.transactions.persistent_views import SigningAcceptButton, SigningDeclineButton

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
        
        # Add persistent buttons
        self.add_item(SigningAcceptButton(pending_signing['id']))
        self.add_item(SigningDeclineButton(pending_signing['id']))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def set_message(self, message: discord.Message) -> None:
        self.message = message

    def refresh_pending(self, pending: Dict[str, Any]) -> None:
        self.pending_signing = pending

    def _decorate_embed(self, embed: discord.Embed) -> None:
        """Add team and league branding to embed."""
        # Add team emoji to title
        from utils.team_data_provider import TeamDataProvider
        provider = TeamDataProvider()
        emoji = provider.get_team_emoji(self.team.get('code'))
        if emoji and embed.title:
            embed.title = f"{emoji} {embed.title}"
        
        # Add league emoji to footer
        from utils.embed_assets import get_league_emoji
        league_emoji = get_league_emoji(self.league_code)
        if league_emoji:
            footer_text = getattr(embed.footer, "text", "") or ""
            if footer_text:
                embed.set_footer(text=f"{league_emoji} {footer_text}")
            else:
                embed.set_footer(text=league_emoji)

    async def _finalize(self, embed: discord.Embed, files: Optional[list] = None) -> None:
        self._decorate_embed(embed)
        self.resolved = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            attachments = list(self.message.attachments) if self.message.attachments else []
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
    # Callbacks are handled by persistent_views.py DynamicItems


    # ------------------------------------------------------------------
    # Embed helpers
    # ------------------------------------------------------------------
    def _decline_embed(self, actor: discord.abc.User) -> tuple[discord.Embed, Optional[list]]:
        builder = EmbedBuilder(
            title="Signing Declined",
            description=f"{self.player.mention} declined the offer from **{self.team['name']}**.",
            force_color=0xED4245,  # Error red
            team_code=self.team.get('code'),
            division_code=self.league_code
        )
        builder.add_field("League", self._league_field_value(), inline=True)
        builder.add_field("Season", self.season_name, inline=True)
        builder.add_field("Player", str(self.player), inline=True)
        builder.add_field("Team", f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        builder.add_field("Updated By", actor.mention, inline=True)
        builder.add_timestamp()
        if self.team.get('code'):
            builder.add_team_image(self.team['code'], position="thumbnail")
        return builder.build_with_files()

    def _already_signed_embed(self) -> tuple[discord.Embed, Optional[list]]:
        builder = EmbedBuilder(
            title="No Changes Needed",
            description=f"{self.player.mention} is already rostered on **{self.team['name']}**.",
            force_color=0xFAA61A,  # Orange/warning color
            team_code=self.team.get('code'),
            division_code=self.league_code
        )
        builder.add_field("League", self._league_field_value(), inline=True)
        builder.add_field("Season", self.season_name, inline=True)
        builder.add_field("Player", str(self.player), inline=True)
        builder.add_field("Team", f"{self.team['name']} (`{self.team['code']}`)", inline=True)
        builder.add_timestamp()
        if self.team.get('code'):
            builder.add_team_image(self.team['code'], position="thumbnail")
        return builder.build_with_files()


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
        logger.info("🔄 Invalidating team player cache for release - team %s", self.team['id'])
        cache = TeamPlayerCache()
        await cache.invalidate_team(self.team['id'])

        # Build embed with EmbedBuilder
        league_value = self.league_display or (self.league_code or self.season.get('league_type') or 'Unknown League')
        builder = EmbedBuilder(
            title="Player Released",
            description=f"{self.player_member.mention} has been released from **{self.team['name']}**.",
            color=discord.Color.orange()
        )
        builder.add_team_field("Team", self.team.get('name'), self.team.get('code'))
        builder.add_league_field("League", league_value, self.league_code)
        builder.add_field("Season", self.season_name, inline=True)
        builder.add_field("Player", str(self.player_member), inline=True)
        builder.add_field("Released By", interaction.user.mention, inline=True)
        if reason:
            builder.add_field("Reason", reason, inline=False)
        builder.add_timestamp()
        builder.add_team_image(self.team.get('code'), as_thumbnail=True)
        
        embed, files = builder.build_with_files()        
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
