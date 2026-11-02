"""Discord cog scaffolding for transaction management."""

from __future__ import annotations

import json
import logging
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

import discord
from discord import app_commands, utils
from discord.ext import commands

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.config_manager import get_config_manager
from utils.team_player_cache import TeamPlayerCache
from utils.autocomplete_helper import AutocompleteHelper
from utils.embed_builder import EmbedBuilder
from utils.branding import get_league_name
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)
from utils.error_handler import ConfirmationView
from utils.team_display import (
    format_team_label_from_record,
    format_team_label_by_id,
    format_team_label_by_code,
)

from .views import PendingSigningView, ReleaseConfirmationModal
from .trade_views import TradeAssetModal
from .history import fetch_filtered_transactions, build_history_embed
from .pick_trading import picks_enabled_for_division
from utils.league_cache import LeagueCache
from db.db_utils import execute_async
from db.league_repository import friendly_error

logger = logging.getLogger(__name__)


async def autocomplete_team_code(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete team codes (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.team_code_choices(current)


async def autocomplete_team_player(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete player names on user's team (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.player_on_team_choices(interaction, current)


async def autocomplete_league(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete league codes (delegates to AutocompleteHelper)."""
    return await AutocompleteHelper.league_choices(current)


class TransactionsCog(commands.GroupCog, name="transactions"):
    """Slash-command entry point for roster transactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)
        self.config = get_config_manager()

        # Lock + tracking for preventing duplicate game recording (matches inspiration/cogs/score/report_score.py)
        self._recording_lock: asyncio.Lock = asyncio.Lock()
        self.active_recordings: Set[str] = set()  # Set of "team1_vs_team2" keys to prevent double-recording

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _get_league_display(self, season: Dict[str, Any]) -> tuple[str, str]:
        """Human-friendly division label and uppercase code for a season dict carrying ``division_id``."""
        division = LeagueCache.division(season.get('division_id')) or {}
        league_name = (division.get('name') or season.get('league_name') or '').strip()
        league_code = (division.get('code') or season.get('league_type') or '').upper()
        display = league_name or league_code or "Unknown Division"
        if league_name and league_code and league_name.upper() != league_code:
            display = f"{league_name} ({league_code})"
        return display, league_code

    async def cog_load(self) -> None:
        """Recover pending offers on cog load (bot restart) and re-attach views so they're interactive.
        
        Note: DynamicItem-based buttons (in persistent_views.py) automatically reattach via regex templates,
        so this recovery primarily ensures messages are still accessible and embeds are up-to-date.
        """
        logger.info("🔄 Recovering pending signing offers from database...")
        try:
            # Query only pending offers (status='pending') since others are terminal
            result = await execute_async(
                self.supabase.table('pending_signings')
                .select('*')
                .eq('status', 'pending')
            )
            pending_offers = result.data or []
            
            if not pending_offers:
                logger.info("✅ No pending offers to recover")
                return
            
            logger.info(f"📋 Found {len(pending_offers)} pending offer(s) to recover")
            
            for offer in pending_offers:
                try:
                    guild_id = offer.get('guild_id')
                    channel_id = offer.get('channel_id')  # Correct field name (not message_channel_id)
                    message_id = offer.get('message_id')
                    player_id = offer.get('player_id')
                    team_id = offer.get('team_id')
                    signing_id = offer.get('id')
                    
                    # Check for missing critical fields
                    if not all([guild_id, channel_id, message_id, player_id, team_id, signing_id]):
                        logger.warning(
                            f"⚠️ Incomplete pending offer record {signing_id}: "
                            f"guild_id={guild_id}, channel_id={channel_id}, message_id={message_id}"
                        )
                        continue
                    
                    # Attempt to fetch the message and validate it's still there
                    try:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            logger.warning(f"⚠️ Guild {guild_id} not found for offer {signing_id}")
                            continue
                        
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            logger.warning(f"⚠️ Channel {channel_id} not found in guild {guild_id} for offer {signing_id}")
                            continue
                        
                        # Verify message exists (but don't edit - persistent views handle button reattachment)
                        try:
                            message = await channel.fetch_message(message_id)
                            logger.info(f"✅ Verified pending offer message {message_id} exists in {channel.mention}")
                        except discord.NotFound:
                            logger.warning(f"⚠️ Message {message_id} no longer exists for offer {signing_id}")
                        except discord.Forbidden:
                            logger.warning(f"⚠️ No permission to fetch message {message_id} for offer {signing_id}")
                            
                    except Exception as exc:  # pragma: no cover - network path
                        logger.error(f"❌ Error verifying offer message {message_id}: {exc}")
                        
                except Exception as exc:  # pragma: no cover - network path
                    logger.error(f"❌ Error recovering offer {offer.get('id')}: {exc}", exc_info=True)
        except Exception as exc:  # pragma: no cover - network path
            logger.error(f"❌ Failed to recover pending offers: {exc}", exc_info=True)

    def _get_matchup_key(self, team1_code: str, team2_code: str) -> str:
        """Generate a unique, order-independent key for a matchup (prevents duplicate recording)."""
        teams = sorted([team1_code.lower(), team2_code.lower()])
        key = f"{teams[0]}_vs_{teams[1]}"
        logger.debug(f"Generated matchup key: {key} from teams {team1_code} and {team2_code}")
        return key

    async def acquire_recording_lock(self, team1: str, team2: str) -> Optional[str]:
        """Attempt to acquire recording lock for a matchup. Returns matchup_key if successful, None if already recording."""
        matchup_key = self._get_matchup_key(team1, team2)
        async with self._recording_lock:
            if matchup_key in self.active_recordings:
                logger.warning(f"Recording already in progress for matchup {matchup_key}")
                return None
            self.active_recordings.add(matchup_key)
            logger.info(f"✅ Acquired recording lock for matchup {matchup_key}. Active: {self.active_recordings}")
            return matchup_key

    async def release_recording_lock(self, matchup_key: str) -> None:
        """Release recording lock for a matchup."""
        async with self._recording_lock:
            self.active_recordings.discard(matchup_key)
            logger.info(f"✅ Released recording lock for matchup {matchup_key}. Remaining active: {self.active_recordings}")

    async def _check_transaction_rules(self, interaction: discord.Interaction, action_type: str, team_id: Optional[str] = None) -> bool:
        """
        Check if a transaction type is enabled globally and if the team is banned.
        Returns True if allowed, False if blocked (and sends response).
        """
        guild_id = interaction.guild_id
        if not guild_id:
            return True

        config = await self.config.get_guild_config(guild_id)
        rules = config.get("transaction_rules", {})
        
        # Debug logging
        logger.info(f"🔍 Transaction rules check: guild={guild_id}, action={action_type}, team_id={team_id}")
        logger.info(f"   Full rules dict: {rules}")
        logger.info(f"   Rules type: {type(rules)}")
        
        # Determine response method based on interaction state
        send_method = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message

        # 1. Check global toggle
        # action_type: "signings", "releases", "trades"
        if not rules.get(f"{action_type}_enabled", True):
            await send_method(
                f"❌ **{action_type.capitalize()} are currently disabled** by league admins.",
                ephemeral=True
            )
            return False

        # 2. Check team ban
        if team_id:
            banned_teams = await queries.get_blocked_teams_for_guild(self.supabase, guild_id)
            logger.info(f"   Banned teams list: {banned_teams}")
            logger.info(f"   Banned teams type: {type(banned_teams)}")
            logger.info(f"   Team ID to check: {team_id} (type: {type(team_id)})")
            
            is_banned = team_id in banned_teams
            logger.info(f"   Is team {team_id} in banned list? {is_banned}")
            
            # Additional debug: Show each item in banned_teams with its type
            for i, banned_id in enumerate(banned_teams):
                logger.info(f"      [{i}] banned_id={banned_id} (type: {type(banned_id).__name__}), matches={banned_id == team_id}")
            
            if is_banned:
                logger.warning(f"🚫 Team {team_id} is banned from {action_type}")
                await send_method(
                    "❌ **Your team is currently banned from making transactions.**\n"
                    "Please contact a league admin for more information.",
                    ephemeral=True
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Signings
    # ------------------------------------------------------------------
    @app_commands.command(name="sign", description="[TEAM OWNER] Begin a signing offer for a player")
    @app_commands.describe(player="Discord member to sign", minutes_valid="Minutes before the offer expires (defaults to config)")
    async def sign(self, interaction: discord.Interaction, player: discord.Member, minutes_valid: Optional[int] = None):
        """Create a pending signing and prompt the player to accept or decline."""
        await interaction.response.defer(ephemeral=True)

        # Check global signing rules
        if not await self._check_transaction_rules(interaction, "signings"):
            return

        # Prevent signing bots or self
        if player.bot:
            await interaction.followup.send("❌ You cannot sign a bot.", ephemeral=True)
            return
        if player.id == interaction.user.id:
            await interaction.followup.send("❌ You cannot sign yourself.", ephemeral=True)
            return

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

        # Check if team is banned from transactions
        if not await self._check_transaction_rules(interaction, "signings", team['id']):
            return

        # Ensure there is an active season and that the team participates in it.
        season = await queries.get_active_season(self.supabase, division_id=team.get('division_id'))
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return
        if not team.get('division_id'):
            await interaction.followup.send(
                "❌ Your team isn't in a division this season.",
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

        existing_roster = await self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if existing_roster and existing_roster.get('team_id') == team['id']:
            await interaction.followup.send("ℹ️ That player is already on your roster.", ephemeral=True)
            return
        if existing_roster and existing_roster.get('team_id') != team['id']:
            await interaction.followup.send(
                "❌ Player is currently rostered elsewhere. They must be released before signing.",
                ephemeral=True,
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

        payload = {
            'guild_id': interaction.guild_id,
            'season_id': season['id'],
            'team_id': team['id'],
            'player_id': player_record['id'],
            'created_by': interaction.user.id,
            'status': 'pending',
            'expires_at': expires_at.isoformat(),
            'metadata': {
                'requested_expiry_minutes': expiry_minutes,
                'coach_discord_id': interaction.user.id,
            }
        }

        pending = await self.repo.create_pending_signing(payload)
        if not pending:
            await interaction.followup.send("❌ Failed to create pending signing. Please try again later.", ephemeral=True)
            return

        league_display, league_code = await self._get_league_display(season)
        files, thumbnail = build_team_logo_payload(team['code'])
        expiry_text = utils.format_dt(expires_at, style='R')
        from utils.embed_builder import EmbedBuilder  # local import to avoid circulars at module load
        team_label = format_team_label_from_record(team)
        builder = EmbedBuilder.generic(
            title="Signing Offer",
            description=f"{player.mention}, {team_label} would like to sign you!",
            team_code=team['code'],
            division_code=league_code,
        )
        builder.add_league_field(league_code)
        builder.add_field("Season", season.get('name', 'Unknown Season'), inline=True)
        builder.add_field("Player", player.display_name, inline=True)
        builder.add_field("Team", team_label, inline=True)
        builder.add_field("Coach", interaction.user.mention, inline=True)
        builder.add_field("Expires", expiry_text, inline=False)
        # Provide instructional footer augmentation (text only, no icon)
        footer_text = builder.embed.footer.text or get_league_name()
        builder.set_footer_text(f"{footer_text} • Respond below to confirm or decline.")
        if thumbnail:
            builder.set_thumbnail_url(thumbnail)
        # Merge files (team logo + existing footer logo already in builder.files)
        builder.files.extend(files)
        embed, files = builder.build_with_files()

        view = PendingSigningView(
            self,
            pending,
            player,
            team,
            season,
            league_display,
            league_code,
            team_logo_filename=thumbnail,
        )

        # Use configured transactions channel when available.
        channel = interaction.channel
        channel_id = transactions_cfg.get('channel_id')
        if channel_id:
            configured_channel = interaction.guild.get_channel(channel_id)
            if isinstance(configured_channel, discord.TextChannel):
                channel = configured_channel

        if channel is None:
            await self.repo.delete_pending_signing(pending['id'])
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
                allowed_mentions=discord.AllowedMentions(users=[player]),
                files=files,
            )
        except discord.Forbidden:
            await self.repo.delete_pending_signing(pending['id'])
            await interaction.followup.send(
                "❌ I don't have permission to post the offer in the configured channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            logger.error("Failed to send signing offer message: %s", exc)
            await self.repo.delete_pending_signing(pending['id'])
            await interaction.followup.send(
                "❌ Something went wrong delivering the offer message.",
                ephemeral=True,
            )
            return

        view.set_message(message)
        pending['message_id'] = message.id
        pending['channel_id'] = message.channel.id
        view.refresh_pending(pending)
        await self.repo.update_pending_signing(pending['id'], {
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
    @app_commands.command(name="release", description="[TEAM OWNER] Release a player from your roster")
    async def release(self, interaction: discord.Interaction, player: discord.Member):
        """Launch modal confirmation to release a player from the roster."""
        # Check global release rules
        if not await self._check_transaction_rules(interaction, "releases"):
            return

        transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)

        team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
        if not team:
            await interaction.response.send_message(
                "❌ Unable to determine your team. Ensure your Discord ID is set as the team owner.",
                ephemeral=True,
            )
            return

        # Check team ban
        if not await self._check_transaction_rules(interaction, "releases", team['id']):
            return

        season = await queries.get_active_season(self.supabase, division_id=team.get('division_id'))
        if not season:
            await interaction.response.send_message("⚠️ No active season configured.", ephemeral=True)
            return
        if not team.get('division_id'):
            await interaction.response.send_message(
                "❌ Your team isn't in a division this season.",
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

        roster_entry = await self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if not roster_entry or roster_entry.get('team_id') != team['id']:
            await interaction.response.send_message(
                "❌ That player is not currently on your active roster.",
                ephemeral=True,
            )
            return

        league_display, league_code = await self._get_league_display(season)

        modal = ReleaseConfirmationModal(
            cog=self,
            player_member=player,
            player_record=player_record,
            team=team,
            season=season,
            channel_id=transactions_cfg.get('channel_id'),
            league_display=league_display,
            league_code=league_code,
        )

        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Admin force signings (bypass confirmation)
    # ------------------------------------------------------------------
    @app_commands.command(name="admin-force-sign", description="[ADMIN] Force sign a player to a team without confirmation")
    @app_commands.describe(
        team_code="Team code (e.g., PANDAS)",
        player="Discord member to sign"
    )
    @app_commands.autocomplete(team_code=autocomplete_team_code)
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_force_sign(
        self,
        interaction: discord.Interaction,
        team_code: str,
        player: discord.Member
    ) -> None:
        """Admin can force sign a player without waiting for confirmation."""
        await interaction.response.defer(ephemeral=True)

        # Resolve team by code
        team = await queries.get_team_by_code(self.supabase, team_code)
        if not team:
            await interaction.followup.send(
                f"❌ Unknown team code `{team_code}`.",
                ephemeral=True,
            )
            return

        # Get the active season for this team's division
        season = await queries.get_active_season(self.supabase, division_id=team.get('division_id'))
        if not season:
            await interaction.followup.send(
                "⚠️ No active season configured for this team's division.",
                ephemeral=True,
            )
            return
        if not team.get('division_id'):
            await interaction.followup.send(
                "❌ That team isn't in a division this season.",
                ephemeral=True,
            )
            return

        # Get or create player record
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

        # Check if player is already on roster (for this season)
        existing_roster = await self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if existing_roster and existing_roster.get('team_id') == team['id']:
            await interaction.followup.send(
                "ℹ️ That player is already on this team's roster.",
                ephemeral=True,
            )
            return

        # If player is on another team, release them first (force)
        if existing_roster and existing_roster.get('team_id') != team['id']:
            try:
                self.repo.release_player_from_roster(
                    existing_roster['id'],
                    reason="Admin force sign (auto-release from previous team)"
                )
                logger.info(
                    f"🔧 AUTO-RELEASE: {player.display_name} from "
                    f"{existing_roster.get('team_id')} for admin force sign"
                )
            except Exception as e:
                logger.error(f"Failed to auto-release player: {e}")
                await interaction.followup.send(
                    "⚠️ Player is on another team and auto-release failed. Try again.",
                    ephemeral=True,
                )
                return

        # Sign the player to the team (no pending offer, direct sign)
        try:
            roster_entry = self.repo.sign_player_to_roster(
                season_id=season['id'],
                team_id=team['id'],
                player_id=player_record['id'],
                reason=f"Admin force sign by {interaction.user.name}#{interaction.user.discriminator}"
            )

            if not roster_entry:
                await interaction.followup.send(
                    "❌ Failed to sign player. Try again.",
                    ephemeral=True,
                )
                return

            # Refresh cache for affected team
            cache = TeamPlayerCache()
            await cache.invalidate_team(team['id'])

            league_display, league_code = await self._get_league_display(season)

            team_label = format_team_label_from_record(team)

            # Log to audit trail
            logger.info(
                f"🔧 ADMIN FORCE SIGN: {player.display_name} → {team_label} "
                f"in {league_display} {season['name']} by {interaction.user.name}"
            )

            # Notify
            files, thumbnail = build_team_logo_payload(team['code'])

            builder = EmbedBuilder(
                title="✅ Admin Force Sign",
                description=f"**{player.display_name}** has been signed to {team_label}",
                team_code=team['code'],
                division_code=league_code,
            )
            builder.add_league_field(league_code)
            builder.add_field("Season", season['name'], inline=True)
            builder.add_field("Player", player.display_name, inline=True)
            builder.add_field("Team", team_label, inline=True)
            builder.add_field("Executed by", interaction.user.mention, inline=False)
            builder.add_timestamp()
            builder.set_footer_text("This was an admin force sign (no confirmation needed)")
            if team['code']:
                builder.add_team_image(team['code'], position="thumbnail")
            builder.files.extend(files)
            embed, all_files = builder.build_with_files()

            # Post to transactions channel
            transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)
            channel_id = transactions_cfg.get('channel_id')
            if channel_id:
                try:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel and isinstance(channel, discord.TextChannel):
                        channel_embed, channel_files = builder.build_with_files()
                        await channel.send(embed=channel_embed, files=channel_files or None)
                except Exception as channel_error:
                    logger.warning(f"Failed to post admin force sign to transactions channel: {channel_error}")

            await interaction.followup.send(embed=embed, ephemeral=True, files=all_files or None)

        except Exception as e:
            logger.error(f"Failed to force sign player: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error: {str(e)}",
                ephemeral=True,
            )

    @app_commands.command(name="admin-force-release", description="[ADMIN] Force release a player from their team without confirmation")
    @app_commands.describe(
        player="Discord member to release"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_force_release(
        self,
        interaction: discord.Interaction,
        player: discord.Member
    ) -> None:
        """Admin can force release a player without confirmation."""
        await interaction.response.defer(ephemeral=True)

        # Get or resolve player
        player_record = await queries.get_player_by_discord_id(self.supabase, player.id)
        if not player_record:
            player_record = await queries.get_player_by_name(self.supabase, player.display_name)
        if not player_record:
            await interaction.followup.send(
                "❌ That player is not registered in the database.",
                ephemeral=True,
            )
            return

        # Find the player's roster entry in the active season (a player is on at most one team).
        active_season = await queries.get_active_season(self.supabase)
        if not active_season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return
        try:
            roster_response = await execute_async(
                self.supabase.table('rosters')
                .select('*')
                .eq('player_id', player_record['id'])
                .eq('season_id', active_season['id'])
                .is_('left_at', 'null')
                .limit(1)
            )
        except Exception as exc:
            logger.error("Failed to fetch active roster entries for %s: %s", player_record['id'], exc)
            await interaction.followup.send(
                "❌ Failed to check the player's roster.",
                ephemeral=True,
            )
            return

        if not roster_response.data:
            await interaction.followup.send(
                "❌ That player is not currently on any active roster.",
                ephemeral=True,
            )
            return

        roster_entry = roster_response.data[0]
        season = await queries.get_active_season(
            self.supabase, division_id=(LeagueCache.assignment(roster_entry['team_id']) or {}).get('division_id'),
        )

        # Release the player
        try:
            self.repo.release_player_from_roster(
                roster_entry['id'],
                reason=f"Admin force release by {interaction.user.name}#{interaction.user.discriminator}"
            )

            # Refresh cache for affected team
            cache = TeamPlayerCache()
            await cache.invalidate_team(roster_entry['team_id'])

            # Get team info for the embed
            team = TeamPlayerCache.get_team(roster_entry['team_id'])
            team_display = format_team_label_from_record(team)

            league_display, league_code = await self._get_league_display(season)

            # Log to audit trail
            logger.info(
                f"🔧 ADMIN FORCE RELEASE: {player.display_name} from {team_display} "
                f"in {league_display} {season['name']} by {interaction.user.name}"
            )

            # Notify
            files, thumbnail = build_team_logo_payload(team['code'] if team else None)

            builder = EmbedBuilder(
                title="🚪 Admin Force Release",
                description=f"**{player.display_name}** has been released from {team_display}",
                team_code=team['code'] if team else None,
                division_code=league_code,
            )
            builder.add_league_field(league_code)
            builder.add_field("Season", season['name'], inline=True)
            builder.add_field("Player", player.display_name, inline=True)
            builder.add_field("Team", team_display, inline=True)
            builder.add_field("Executed by", interaction.user.mention, inline=False)
            builder.add_timestamp()
            builder.set_footer_text("This was an admin force release (no confirmation needed)")
            if team and team.get('code'):
                builder.add_team_image(team['code'], position="thumbnail")
            builder.files.extend(files)
            embed, all_files = builder.build_with_files()

            # Post to transactions channel
            transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)
            channel_id = transactions_cfg.get('channel_id')
            if channel_id:
                try:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel and isinstance(channel, discord.TextChannel):
                        channel_embed, channel_files = builder.build_with_files()
                        await channel.send(embed=channel_embed, files=channel_files or None)
                except Exception as channel_error:
                    logger.warning(f"Failed to post admin force release to transactions channel: {channel_error}")

            await interaction.followup.send(embed=embed, ephemeral=True, files=all_files or None)

        except Exception as e:
            logger.error(f"Failed to force release player: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error: {str(e)}",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # Draft picks
    # ------------------------------------------------------------------
    @app_commands.command(name="picks", description="[MEMBERS] Show draft pick ownership for the active season")
    @app_commands.describe(
        team_code="Optional team code to filter by current owner",
        division="Division whose draft to show (defaults to the team's division)",
    )
    @app_commands.autocomplete(team_code=autocomplete_team_code, division=autocomplete_league)
    async def picks(self, interaction: discord.Interaction, team_code: Optional[str] = None,
                    division: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        await LeagueCache.ensure_ready()

        target_team = None
        if team_code:
            target_team = TeamPlayerCache.find_team(team_code)
            if not target_team:
                await interaction.followup.send(f"❌ Unknown team code `{team_code}`.", ephemeral=True)
                return

        if division:
            division_row = LeagueCache.find_division(division)
            if not division_row:
                await interaction.followup.send(f"❌ Unknown division `{division}`.", ephemeral=True)
                return
        elif target_team:
            division_row = LeagueCache.team_division(target_team['id'])
        else:
            draft_divisions = LeagueCache.picks_divisions()
            division_row = draft_divisions[0] if len(draft_divisions) == 1 else None
            if len(draft_divisions) > 1:
                names = ", ".join(d['code'] for d in draft_divisions)
                await interaction.followup.send(f"ℹ️ Choose a division: {names}.", ephemeral=True)
                return

        if not division_row or not picks_enabled_for_division(division_row['id']):
            await interaction.followup.send("ℹ️ That division doesn't run a draft.", ephemeral=True)
            return

        season = await queries.get_active_season(self.supabase, division_id=division_row['id'])
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

        league_display, _ = await self._get_league_display(season)
        league_season_display = f"{league_display} — {season.get('name', 'Unknown Season')}"

        picks = await self.repo.list_draft_picks(season['id'], division_row['id'])
        if not picks:
            await interaction.followup.send(
                f"ℹ️ No draft picks found for {league_season_display}.",
                ephemeral=True,
            )
            return

        teams = await queries.get_all_teams(self.supabase, division_row['id'])
        team_by_id = {team['id']: team for team in teams}

        def team_label(team_id: Optional[str]) -> str:
            if not team_id:
                return "Unassigned"
            team_row = team_by_id.get(team_id)
            return format_team_label_from_record(team_row, fallback="Unknown Team")

        grouped = defaultdict(list)
        for pick in picks:
            if target_team and pick['current_owner_team_id'] != target_team['id']:
                continue
            grouped[pick['round']].append(pick)

        if target_team and not grouped:
            await interaction.followup.send(
                f"ℹ️ {target_team['name']} does not currently own any picks in {league_season_display}.",
                ephemeral=True,
            )
            return

        league_display, league_code = await self._get_league_display(season)
        builder = EmbedBuilder(
            title="Draft Pick Ownership",
            description=f"Season {season['name']} picks",
            team_code=target_team.get('code') if target_team else None,
            division_code=league_code
        )
        if target_team:
            builder.set_footer_text(f"Filtered for {target_team['name']} ({target_team['code']})")

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
            builder.add_field(f"Round {rnd}", value[:1024], inline=False)

        if not builder.embed.fields:
            builder.embed.description = "No picks to display after applying filters."

        if target_team and target_team.get('code'):
            builder.add_team_image(target_team['code'], position="thumbnail")

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    @app_commands.command(name="trade", description="[TEAM OWNER] Propose a multi-asset trade with another team")
    @app_commands.describe(counterparty_team="Team code (e.g., 'pandas') to trade with")
    @app_commands.autocomplete(counterparty_team=autocomplete_team_code)
    async def trade(self, interaction: discord.Interaction, counterparty_team: str):
        """Initiate a trade proposal with multi-asset selection modal."""
        # NOTE: Do NOT defer here - we need to send a modal as the first response
        
        # Use cache for fast lookups (no async DB calls before modal)
        cache = TeamPlayerCache()
        
        # Identify initiator's team from cache
        team = cache.get_team_by_owner(interaction.user.id)
        if not team:
            await interaction.response.send_message(
                "❌ Unable to determine your team. Ensure your Discord ID is set as the team owner.",
                ephemeral=True,
            )
            return

        season = await queries.get_active_season(self.supabase, division_id=team.get('division_id'))
        if not season:
            await interaction.response.send_message("⚠️ No active season configured.", ephemeral=True)
            return
        if not team.get('division_id'):
            await interaction.response.send_message(
                "❌ Your team isn't in a division this season.",
                ephemeral=True,
            )
            return

        # Resolve counterparty team from cache
        counterparty = cache.get_team_by_code(counterparty_team)
        if not counterparty:
            await interaction.response.send_message(
                f"❌ Unknown team code `{counterparty_team}`.",
                ephemeral=True,
            )
            return
        if counterparty['id'] == team['id']:
            await interaction.response.send_message(
                "❌ You cannot trade with your own team.",
                ephemeral=True,
            )
            return

        if not counterparty.get('division_id'):
            await interaction.response.send_message(
                f"❌ {counterparty.get('name', counterparty_team)} isn't in a division this season.",
                ephemeral=True,
            )
            return
        # Seasons are league-wide; the counterparty's season dict just carries its own division.
        counterparty_season = {**season, 'division_id': counterparty['division_id']}

        modal = TradeAssetModal(
            cog=self,
            initiating_team=team,
            counterparty_team=counterparty,
            season=season,
            counterparty_season=counterparty_season,
            supabase=self.supabase
        )

        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Team Roster
    # ------------------------------------------------------------------
    @app_commands.command(name="roster", description="[MEMBERS] View a team's roster for the active season")
    @app_commands.describe(team_code="Team code (e.g., PANDAS)")
    @app_commands.autocomplete(team_code=autocomplete_team_code)
    async def view_roster(self, interaction: discord.Interaction, team_code: str):
        """Display a team's current roster including owner and all active players."""
        await interaction.response.defer(ephemeral=True)

        # Resolve team by code
        team = await queries.get_team_by_code(self.supabase, team_code)
        if not team:
            await interaction.followup.send(
                f"❌ Unknown team code `{team_code}`.",
                ephemeral=True,
            )
            return

        season = await queries.get_active_season(self.supabase, division_id=team.get('division_id'))
        if not season:
            await interaction.followup.send(
                "⚠️ No active season configured for that team's division.",
                ephemeral=True,
            )
            return

        if not team.get('division_id'):
            await interaction.followup.send(
                "❌ That team is not in the active season's division.",
                ephemeral=True,
            )
            return

        # Get active rosters for this team in this season
        rosters = await queries.get_active_rosters(self.supabase, season['id'])
        team_rosters = [r for r in rosters if r.get('team_id') == team['id']]

        # Build roster list with player details
        roster_lines = []
        for roster in sorted(team_rosters, key=lambda r: r.get('players', {}).get('display_name', '')):
            player_data = roster.get('players', {})
            player_name = player_data.get('display_name', 'Unknown')
            roster_lines.append(f"🧑 **{player_name}**")

        # Get team owner info
        owner_id = team.get('owner_discord_id')
        owner_mention = f"<@{owner_id}>" if owner_id else "Unknown"

        team_label = format_team_label_from_record(team)

        league_display, league_code = await self._get_league_display(season)
        
        builder = EmbedBuilder(
            title=f"📋 {team_label} Roster",
            description=f"Season: **{season['name']}**",
            team_code=team.get('code'),
            division_code=league_code
        )
        builder.add_field("Team", team_label, inline=True)
        builder.add_league_field(league_code)
        builder.add_field("Owner", owner_mention, inline=True)

        if roster_lines:
            builder.add_field(
                f"Players ({len(roster_lines)})",
                "\n".join(roster_lines),
                inline=False
            )
        else:
            builder.add_field("Players", "No players on roster yet.", inline=False)

        builder.add_timestamp()
        if team.get('code'):
            builder.add_team_image(team['code'], position="thumbnail")

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

    # ------------------------------------------------------------------
    # Transaction history
    # ------------------------------------------------------------------
    @app_commands.command(name="history", description="[MEMBERS] View transaction history with optional filters")
    @app_commands.describe(
        league="Optional division code to filter by (e.g. 'D1')",
        team_code="Optional team code to filter by (e.g., 'pandas')",
        player_name="Optional player name to filter by",
        txn_type="Optional transaction type: sign, release, trade",
        days="Look back this many days (default 30)"
    )
    @app_commands.autocomplete(team_code=autocomplete_team_code, league=autocomplete_league)
    async def history(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        team_code: Optional[str] = None,
        player_name: Optional[str] = None,
        txn_type: Optional[str] = None,
        days: int = 30
    ):
        """Display transaction history with optional filters."""
        await interaction.response.defer(ephemeral=True)

        if days < 1 or days > 365:
            await interaction.followup.send("❌ Days must be between 1 and 365.", ephemeral=True)
            return

        if txn_type and txn_type.lower() not in ('sign', 'release', 'trade'):
            await interaction.followup.send(
                "❌ Transaction type must be one of: sign, release, trade.",
                ephemeral=True,
            )
            return

        division = None
        if league:
            division = await queries.get_division_by_code(self.supabase, league)
            if not division:
                await interaction.followup.send(
                    f"❌ Unknown division `{league}`.",
                    ephemeral=True,
                )
                return

        team = None
        if team_code:
            team = await queries.get_team_by_code(self.supabase, team_code)
            if not team:
                await interaction.followup.send(
                    f"❌ Unknown team code `{team_code}`.",
                    ephemeral=True,
                )
                return
            if division and team.get('division_id') != division['id']:
                await interaction.followup.send(
                    "❌ That team is not in the selected division.",
                    ephemeral=True,
                )
                return

        division_id = division['id'] if division else (team.get('division_id') if team else None)
        season = await queries.get_active_season(self.supabase, division_id=division_id)
        if not season:
            if division_id:
                await interaction.followup.send(
                    "⚠️ No active season configured for that division.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

        transactions = await fetch_filtered_transactions(
            repo=self.repo,
            supabase=self.supabase,
            season_id=season['id'],
            team_code=team_code,
            player_name=player_name,
            txn_type=txn_type.lower() if txn_type else None,
            days=days
        )

        if division and transactions:
            division_team_ids = set(LeagueCache.team_ids_in_division(division['id'], season['id']))
            transactions = [
                txn for txn in transactions
                if {txn.get('from_team_id'), txn.get('to_team_id')} & division_team_ids
            ]

        if not transactions:
            await interaction.followup.send("ℹ️ No transactions match the filters.", ephemeral=True)
            return

        # Load teams and players for display
        teams = await queries.get_all_teams(self.supabase)
        team_by_id = {team['id']: team for team in teams}

        players = (await execute_async(self.supabase.table('players').select('id, display_name'))).data or []
        player_by_id = {p['id']: p for p in players}

        embed = build_history_embed(transactions, team_by_id, player_by_id)
        _, league_code = await self._get_league_display(season)
        apply_league_badge(embed, league_code)

        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(
            "📋 Displayed %d transactions (filters: team=%s, player=%s, type=%s, days=%d)",
            len(transactions),
            team_code or "none",
            player_name or "none",
            txn_type or "none",
            days
        )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    @app_commands.command(name="config", description="[MEMBERS] View transaction configuration for this guild")
    async def view_config(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        transactions_cfg = await self.config.get_transactions_config(interaction.guild_id)

        builder = EmbedBuilder.generic(
            title="⚙️ Transactions Configuration",
            description="Guild transaction settings"
        )
        if not transactions_cfg:
            builder.embed.description = "No transaction configuration stored for this guild yet."
        else:
            for key, value in transactions_cfg.items():
                builder.add_field(key, str(value), inline=False)

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

    @app_commands.command(name="config-set-channel", description="[ADMIN] Set the channel used for transaction offers")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config_set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        await self.config.set_channel(interaction.guild_id, 'transactions', channel)
        await self.config.update_transactions_config(interaction.guild_id, channel_id=channel.id)

        await interaction.followup.send(
            f"✅ Transactions channel set to {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(name="config-set-expiry", description="[ADMIN] Set default expiry (minutes) for signing offers")
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

    # ------------------------------------------------------------------
    # Team owner commands
    # ------------------------------------------------------------------
    @app_commands.command(name="transfer-ownership", description="[OWNER] Transfer team ownership to another member")
    @app_commands.describe(new_owner="The member to transfer ownership to")
    async def transfer_ownership(self, interaction: discord.Interaction, new_owner: discord.Member):
        """Allow team owner to transfer ownership to another member."""
        await interaction.response.defer(ephemeral=True)

        if new_owner.bot:
            await interaction.followup.send("You cannot transfer ownership to a bot.", ephemeral=True)
            return

        if new_owner.id == interaction.user.id:
            await interaction.followup.send("You cannot transfer ownership to yourself.", ephemeral=True)
            return

        # Find team owned by this user
        team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
        if not team:
            await interaction.followup.send(
                "You do not own a team. Only team owners can transfer ownership.",
                ephemeral=True,
            )
            return

        # Check if new owner already owns a team
        existing_team = await queries.get_team_by_owner(self.supabase, new_owner.id)
        if existing_team:
            await interaction.followup.send(
                f"{new_owner.mention} already owns **{existing_team.get('name')}**. "
                "A member can only own one team.",
                ephemeral=True,
            )
            return

        # Confirm transfer
        from utils.team_display import format_team_label_from_record
        team_label = format_team_label_from_record(team)

        confirm_embed = discord.Embed(
            title="Confirm Ownership Transfer",
            description=(
                f"You are about to transfer ownership of {team_label} to {new_owner.mention}.\n\n"
                "This action cannot be undone without admin intervention."
            ),
            color=discord.Color.orange(),
        )

        class ConfirmTransferView(discord.ui.View):
            def __init__(self, cog, team_data, new_owner_member):
                super().__init__(timeout=60)
                self.cog = cog
                self.team_data = team_data
                self.new_owner_member = new_owner_member
                self.confirmed = False

            @discord.ui.button(label="Confirm Transfer", style=discord.ButtonStyle.danger)
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                await btn_interaction.response.defer(ephemeral=True)
                
                try:
                    # Update team owner
                    update_result = await execute_async(self.cog.supabase.table('teams').update({
                        'owner_discord_id': self.new_owner_member.id,
                    }).eq('id', self.team_data['id']))

                    if not update_result.data:
                        await btn_interaction.followup.send(
                            "Failed to transfer ownership. Please try again.",
                            ephemeral=True,
                        )
                        return

                    # Refresh cache
                    await TeamPlayerCache.refresh(self.cog.supabase)

                    # Post announcement
                    from utils.config_manager import get_config_manager
                    from utils.embed_builder import EmbedBuilder
                    
                    config_mgr = get_config_manager()
                    tx_config = await config_mgr.get_transactions_config(btn_interaction.guild_id)
                    channel_id = tx_config.get('channel_id')

                    # Get league code
                    division = LeagueCache.team_division(self.team_data.get('id')) or {}
                    league_code = (division.get('code') or '').upper()

                    if channel_id:
                        try:
                            channel = btn_interaction.guild.get_channel(int(channel_id))
                            if channel and isinstance(channel, discord.TextChannel):
                                builder = EmbedBuilder(
                                    title="Ownership Transfer",
                                    description=f"{self.new_owner_member.mention} is now the owner of {team_label}",
                                    team_code=self.team_data.get('code'),
                                    division_code=league_code,
                                )
                                builder.add_league_field(league_code)
                                builder.add_field("Team", team_label, inline=True)
                                builder.add_field("New Owner", self.new_owner_member.mention, inline=True)
                                builder.add_field("Previous Owner", btn_interaction.user.mention, inline=True)
                                builder.add_timestamp()
                                builder.add_team_image(self.team_data.get('code'), position="thumbnail")
                                
                                embed, files = builder.build_with_files()
                                await channel.send(
                                    content=self.new_owner_member.mention,
                                    embed=embed,
                                    files=files or None,
                                    allowed_mentions=discord.AllowedMentions(users=[self.new_owner_member]),
                                )
                        except Exception as e:
                            logger.warning(f"Failed to post ownership transfer announcement: {e}")

                    self.confirmed = True
                    for item in self.children:
                        item.disabled = True

                    await btn_interaction.followup.send(
                        f"Ownership of {team_label} has been transferred to {self.new_owner_member.mention}.",
                        ephemeral=True,
                    )

                    logger.info(
                        "Ownership transfer: %s from %s to %s",
                        self.team_data.get('code'),
                        btn_interaction.user.id,
                        self.new_owner_member.id
                    )

                except Exception as e:
                    logger.error(f"Error transferring ownership: {e}", exc_info=True)
                    await btn_interaction.followup.send(
                        friendly_error(e, "Failed to transfer ownership. Please try again."),
                        ephemeral=True,
                    )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                await btn_interaction.response.defer(ephemeral=True)
                for item in self.children:
                    item.disabled = True
                await btn_interaction.followup.send("Ownership transfer cancelled.", ephemeral=True)
                self.stop()

        view = ConfirmTransferView(self, team, new_owner)
        await interaction.followup.send(embed=confirm_embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TransactionsCog(bot))
    logger.info("✅ TransactionsCog loaded")
