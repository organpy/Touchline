"""Discord cog scaffolding for transaction management."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands, utils
from discord.ext import commands

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.config_manager import get_config_manager

from .views import PendingSigningView, ReleaseConfirmationModal

logger = logging.getLogger(__name__)


class TransactionsCog(commands.GroupCog, name="transactions"):
    """Slash-command entry point for roster transactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)
        self.config = get_config_manager()

    # ------------------------------------------------------------------
    # Signings
    # ------------------------------------------------------------------
    @app_commands.command(name="sign", description="Begin a signing offer for a player")
    @app_commands.describe(player="Discord member to sign", minutes_valid="Minutes before the offer expires (defaults to config)")
    async def sign(self, interaction: discord.Interaction, player: discord.Member, minutes_valid: Optional[int] = None):
        """Create a pending signing and prompt the player to accept or decline."""
        await interaction.response.defer(ephemeral=True)

        # Fetch config defaults + validate expiry window.
        transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)
        expiry_minutes = minutes_valid or transactions_cfg.get('default_expiry_minutes', 60)
        if expiry_minutes < 1 or expiry_minutes > 1440:
            await interaction.followup.send("❌ Expiry window must be between 1 and 1440 minutes.", ephemeral=True)
            return

        # Identify command author's team via ownership.
        team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
        if not team:
            await interaction.followup.send(
                "❌ Unable to determine your team. Ensure your Discord ID is set as the team owner.",
                ephemeral=True,
            )
            return

        # Ensure there is an active season and that the team participates in it.
        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return
        if team['division_id'] != season['division_id']:
            await interaction.followup.send(
                "❌ Your team is not part of the active season's division.",
                ephemeral=True,
            )
            return

        # Map Discord member to player record.
        player_record = await queries.get_player_by_discord_id(self.supabase, player.id)
        if not player_record:
            player_record = await queries.get_or_create_player(
                self.supabase,
                player.display_name,
                player.id
            )
        if not player_record:
            await interaction.followup.send("❌ Failed to resolve player profile.", ephemeral=True)
            return

        existing_roster = self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if existing_roster and existing_roster.get('team_id') == team['id']:
            await interaction.followup.send("ℹ️ That player is already on your roster.", ephemeral=True)
            return
        if existing_roster and existing_roster.get('team_id') != team['id']:
            await interaction.followup.send(
                "❌ Player is currently rostered elsewhere. They must be released before signing.",
                ephemeral=True,
            )
            return

        expires_at = datetime.utcnow() + timedelta(minutes=expiry_minutes)

        payload = {
            'guild_id': interaction.guild_id,
            'season_id': season['id'],
            'team_id': team['id'],
            'player_id': player_record['id'],
            'created_by': interaction.user.id,
            'status': 'pending',
            'expires_at': expires_at.isoformat() + 'Z',
            'metadata': {
                'requested_expiry_minutes': expiry_minutes,
                'coach_discord_id': interaction.user.id,
            }
        }

        pending = self.repo.create_pending_signing(payload)
        if not pending:
            await interaction.followup.send("❌ Failed to create pending signing. Please try again later.", ephemeral=True)
            return

        # Compose announcement details.
        expiry_text = utils.format_dt(expires_at, style='R')
        embed = discord.Embed(
            title="Signing Offer",
            description=f"{player.mention}, **{team['name']}** would like to sign you!",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Team", value=f"{team['name']} (`{team['code']}`)")
        embed.add_field(name="Coach", value=interaction.user.mention)
        embed.add_field(name="Expires", value=expiry_text, inline=False)

        view = PendingSigningView(self, pending, player, team, season)

        # Use configured transactions channel when available.
        channel = interaction.channel
        channel_id = transactions_cfg.get('channel_id')
        if channel_id:
            configured_channel = interaction.guild.get_channel(channel_id)
            if isinstance(configured_channel, discord.TextChannel):
                channel = configured_channel

        if channel is None:
            self.repo.delete_pending_signing(pending['id'])
            await interaction.followup.send(
                "❌ Unable to locate a channel to post the signing offer.",
                ephemeral=True,
            )
            return

        try:
            message = await channel.send(
                content=f"{player.mention} you have a new signing offer!",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=[player])
            )
        except discord.Forbidden:
            self.repo.delete_pending_signing(pending['id'])
            await interaction.followup.send(
                "❌ I don't have permission to post the offer in the configured channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.error("Failed to send signing offer message: %s", exc)
            self.repo.delete_pending_signing(pending['id'])
            await interaction.followup.send(
                "❌ Something went wrong delivering the offer message.",
                ephemeral=True,
            )
            return

        view.set_message(message)
        pending['message_id'] = message.id
        pending['channel_id'] = message.channel.id
        view.refresh_pending(pending)
        self.repo.update_pending_signing(pending['id'], {
            'message_id': message.id,
            'channel_id': message.channel.id
        })

        await interaction.followup.send(
            f"✅ Signing offer sent to {player.mention} in {message.channel.mention}.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Releases
    # ------------------------------------------------------------------
    @app_commands.command(name="release", description="Release a player from your roster")
    async def release(self, interaction: discord.Interaction, player: discord.Member):
        """Launch modal confirmation to release a player from the roster."""
        transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)

        team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
        if not team:
            await interaction.response.send_message(
                "❌ Unable to determine your team. Ensure your Discord ID is set as the team owner.",
                ephemeral=True,
            )
            return

        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.response.send_message("⚠️ No active season configured.", ephemeral=True)
            return
        if team['division_id'] != season['division_id']:
            await interaction.response.send_message(
                "❌ Your team is not part of the active season's division.",
                ephemeral=True,
            )
            return

        player_record = await queries.get_player_by_discord_id(self.supabase, player.id)
        if not player_record:
            player_record = await queries.get_player_by_name(self.supabase, player.display_name)
        if not player_record:
            await interaction.response.send_message(
                "❌ That player is not registered in the database.",
                ephemeral=True,
            )
            return

        roster_entry = self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if not roster_entry or roster_entry.get('team_id') != team['id']:
            await interaction.response.send_message(
                "❌ That player is not currently on your active roster.",
                ephemeral=True,
            )
            return

        modal = ReleaseConfirmationModal(
            cog=self,
            player_member=player,
            player_record=player_record,
            team=team,
            season=season,
            channel_id=transactions_cfg.get('channel_id')
        )

        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Draft picks
    # ------------------------------------------------------------------
    @app_commands.command(name="picks", description="Show draft pick ownership for the active season")
    @app_commands.describe(team_code="Optional team code to filter by current owner")
    async def picks(self, interaction: discord.Interaction, team_code: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)

        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

        picks = self.repo.list_draft_picks(season['id'])
        if not picks:
            await interaction.followup.send("ℹ️ No draft picks found for the active season.", ephemeral=True)
            return

        teams = await queries.get_all_teams(self.supabase, season['division_id'])
        team_by_id = {team['id']: team for team in teams}
        team_by_code = {team['code'].upper(): team for team in teams}

        target_team = None
        if team_code:
            target_team = team_by_code.get(team_code.upper())
            if not target_team:
                await interaction.followup.send(
                    f"❌ Unknown team code `{team_code}` for the active division.",
                    ephemeral=True,
                )
                return

        def team_label(team_id: Optional[str]) -> str:
            if not team_id:
                return "Unassigned"
            team_row = team_by_id.get(team_id)
            if not team_row:
                return "Unknown Team"
            return f"{team_row['name']} (`{team_row['code']}`)"

        grouped = defaultdict(list)
        for pick in picks:
            if target_team and pick['current_owner_team_id'] != target_team['id']:
                continue
            grouped[pick['round']].append(pick)

        if target_team and not grouped:
            await interaction.followup.send(
                f"ℹ️ {target_team['name']} does not currently own any picks.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Draft Pick Ownership",
            description=f"Season {season['name']} picks",
            color=discord.Color.purple()
        )
        if target_team:
            embed.set_footer(text=f"Filtered for {target_team['name']} ({target_team['code']})")

        for rnd in sorted(grouped.keys()):
            picks_in_round = sorted(grouped[rnd], key=lambda p: p['overall'])
            lines = []
            for pick in picks_in_round:
                owner = team_label(pick['current_owner_team_id'])
                original = team_label(pick['original_team_id'])
                lines.append(f"Pick {pick['overall']:02d} → {owner}\n└︎ Original: {original}")
            value = "\n".join(lines)
            if not value:
                continue
            embed.add_field(name=f"Round {rnd}", value=value[:1024], inline=False)

        if not embed.fields:
            embed.description = "No picks to display after applying filters."

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @app_commands.command(name="config", description="View transaction configuration for this guild")
    async def view_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)

        embed = discord.Embed(title="⚙️ Transactions Configuration", color=discord.Color.blurple())
        if not transactions_cfg:
            embed.description = "No transaction configuration stored for this guild yet."
        else:
            for key, value in transactions_cfg.items():
                embed.add_field(name=key, value=str(value), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="config-set-channel", description="Set the channel used for transaction offers")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        await self.config.set_channel(interaction.guild_id, 'transactions', channel)
        await self.config.update_transactions_config(interaction.guild_id, channel_id=channel.id)

        await interaction.followup.send(
            f"✅ Transactions channel set to {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(name="config-set-expiry", description="Set default expiry (minutes) for signing offers")
    @app_commands.describe(minutes="Offer expiry in minutes (1-1440)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_set_expiry(self, interaction: discord.Interaction, minutes: int):
        await interaction.response.defer(ephemeral=True)

        if minutes < 1 or minutes > 1440:
            await interaction.followup.send("❌ Expiry must be between 1 and 1440 minutes.", ephemeral=True)
            return

        await self.config.update_transactions_config(
            interaction.guild_id,
            default_expiry_minutes=minutes
        )

        await interaction.followup.send(
            f"⏱️ Default signing expiry set to {minutes} minute(s).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TransactionsCog(bot))
    logger.info("✅ TransactionsCog loaded")
