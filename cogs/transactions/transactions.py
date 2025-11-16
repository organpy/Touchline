"""Discord cog scaffolding for transaction management."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import discord
from discord import app_commands, utils
from discord.ext import commands

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.config_manager import get_config_manager
from utils.team_player_cache import TeamPlayerCache
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)

from .views import PendingSigningView, ReleaseConfirmationModal
from .trade_views import TradeAssetModal
from .history import fetch_filtered_transactions, build_history_embed

logger = logging.getLogger(__name__)



# ==================== AUTOCOMPLETE FUNCTIONS ====================

async def autocomplete_team_code(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete team codes from cache."""
    cache = TeamPlayerCache()
    all_codes = cache.get_team_codes()
    
    # Filter to matching codes
    filtered = [code for code in all_codes if current.upper() in code]
    
    # Return as choices (max 25)
    return [
        app_commands.Choice(name=code, value=code)
        for code in filtered[:25]
    ]


async def autocomplete_team_player(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete player names on user's team."""
    cache = TeamPlayerCache()
    supabase = get_supabase()
    
    # Get user's team
    team = await queries.get_team_by_owner(supabase, interaction.user.id)
    if not team:
        return []
    
    # Get players on that team
    player_names = cache.get_team_player_names(team['id'])
    
    # Filter to matching names
    filtered = [name for name in player_names if current.lower() in name.lower()]
    
    # Return as choices (max 25)
    return [
        app_commands.Choice(name=name, value=name)
        for name in filtered[:25]
    ]


class TransactionsCog(commands.GroupCog, name="transactions"):
    """Slash-command entry point for roster transactions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)
        self.config = get_config_manager()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_league_display(self, season: Dict[str, Any]) -> tuple[str, str]:
        """Return human-friendly league string and uppercase code for a season."""
        league_code = (season.get('league_type') or '').upper()
        league_name = ''

        division_id = season.get('division_id')
        if division_id:
            try:
                response = (
                    self.supabase
                    .table('divisions')
                    .select('name, league_type')
                    .eq('id', division_id)
                    .limit(1)
                    .execute()
                )
                division = (response.data or [None])[0]
                if division:
                    league_name = (division.get('name') or '').strip()
                    division_code = division.get('league_type')
                    if division_code:
                        league_code = division_code.upper()
            except Exception as exc:  # pragma: no cover - network path
                logger.error(
                    "Failed to resolve league metadata for season %s: %s",
                    season.get('id'),
                    exc,
                )

        display = league_name or league_code or "Unknown League"
        if league_name and league_code and league_name.upper() != league_code:
            display = f"{league_name} ({league_code})"

        return display, league_code

    async def cog_load(self) -> None:
        """Recover pending offers on cog load (bot restart)."""
        logger.info("🔄 Recovering pending signing offers from database...")
        try:
            result = self.supabase.table('pending_signings').select('*').execute()
            pending_offers = result.data or []
            
            if not pending_offers:
                logger.info("✅ No pending offers to recover")
                return
            
            logger.info(f"📋 Found {len(pending_offers)} pending offer(s) to recover")
            
            for offer in pending_offers:
                try:
                    guild_id = offer.get('guild_id')
                    channel_id = offer.get('message_channel_id')
                    message_id = offer.get('message_id')
                    player_id = offer.get('player_id')
                    team_id = offer.get('team_id')
                    
                    if not all([guild_id, channel_id, message_id, player_id, team_id]):
                        logger.warning(f"⚠️ Incomplete pending offer record: {offer['id']}")
                        continue
                    
                    # Attempt to fetch the message
                    try:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            logger.warning(f"⚠️ Guild {guild_id} not found for offer {offer['id']}")
                            continue
                        
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            logger.warning(f"⚠️ Channel {channel_id} not found for offer {offer['id']}")
                            continue
                        
                        message = await channel.fetch_message(message_id)
                        logger.info(f"✅ Recovered pending offer message {message_id} in {channel.mention}")
                    except discord.NotFound:
                        logger.warning(f"⚠️ Message {message_id} no longer exists for offer {offer['id']}")
                    except discord.Forbidden:
                        logger.warning(f"⚠️ No permission to fetch message {message_id} for offer {offer['id']}")
                    except discord.HTTPException as e:
                        logger.error(f"❌ Failed to fetch message {message_id}: {e}")
                        
                except Exception as exc:  # pragma: no cover - network path
                    logger.error(f"❌ Error recovering offer {offer.get('id')}: {exc}")
        except Exception as exc:  # pragma: no cover - network path
            logger.error(f"❌ Failed to recover pending offers: {exc}")

    # ------------------------------------------------------------------
    # Signings
    # ------------------------------------------------------------------
    @app_commands.command(name="sign", description="[TEAM OWNER] Begin a signing offer for a player")
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

        league_display, league_code = self._get_league_display(season)
        files, thumbnail = build_team_logo_payload(team['code'])
        # Compose announcement details.
        expiry_text = utils.format_dt(expires_at, style='R')
        embed = discord.Embed(
            title="Signing Offer",
            description=f"{player.mention}, **{team['name']}** would like to sign you!",
            color=discord.Color.blurple()
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Season", value=season.get('name', 'Unknown Season'), inline=True)
        embed.add_field(name="Player", value=player.display_name, inline=True)
        embed.add_field(name="Team", value=f"{team['name']} (`{team['code']}`)", inline=True)
        embed.add_field(name="Coach", value=interaction.user.mention, inline=True)
        embed.add_field(name="Expires", value=expiry_text, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text="Respond with the buttons below to confirm or decline the offer.")
        if thumbnail:
            target_thumbnail(embed, thumbnail)
        apply_team_badge(embed, team['code'])
        apply_league_badge(embed, league_code)

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
                allowed_mentions=discord.AllowedMentions(users=[player]),
                files=files,
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
    @app_commands.command(name="release", description="[TEAM OWNER] Release a player from your roster")
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

        league_display, league_code = self._get_league_display(season)

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
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_force_sign(
        self,
        interaction: discord.Interaction,
        team_code: str,
        player: discord.Member
    ) -> None:
        """Admin can force sign a player without waiting for confirmation."""
        await interaction.response.defer(ephemeral=True)

        # Get active season
        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

        # Resolve team by code
        team = await queries.get_team_by_code(self.supabase, team_code)
        if not team:
            await interaction.followup.send(
                f"❌ Unknown team code `{team_code}`.",
                ephemeral=True,
            )
            return
        if team['division_id'] != season['division_id']:
            await interaction.followup.send(
                "❌ Team is not in the active season's division.",
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
        existing_roster = self.repo.get_active_roster_entry(season['id'], player_record['id'])
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

            # Refresh cache
            await TeamPlayerCache.refresh()

            league_display, league_code = self._get_league_display(season)

            # Log to audit trail
            logger.info(
                f"🔧 ADMIN FORCE SIGN: {player.display_name} → {team['code']} "
                f"in {league_display} {season['name']} by {interaction.user.name}"
            )

            # Notify
            files, thumbnail = build_team_logo_payload(team['code'])

            embed = discord.Embed(
                title="✅ Admin Force Sign",
                color=discord.Color.green()
            )
            embed.add_field(name="Player", value=player.mention, inline=False)
            embed.add_field(name="Team", value=f"{team['name']} (`{team['code']}`)", inline=False)
            embed.add_field(name="League", value=league_display, inline=False)
            embed.add_field(name="Season", value=season['name'], inline=False)
            embed.add_field(name="Admin", value=interaction.user.mention, inline=False)
            embed.set_footer(text="This was an admin force sign (no confirmation needed)")
            if thumbnail:
                target_thumbnail(embed, thumbnail)
            apply_team_badge(embed, team['code'])
            apply_league_badge(embed, league_code)

            await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

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

        # Get active season
        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

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

        # Check if player is on a roster
        roster_entry = self.repo.get_active_roster_entry(season['id'], player_record['id'])
        if not roster_entry:
            await interaction.followup.send(
                "❌ That player is not currently on any active roster.",
                ephemeral=True,
            )
            return

        # Release the player
        try:
            self.repo.release_player_from_roster(
                roster_entry['id'],
                reason=f"Admin force release by {interaction.user.name}#{interaction.user.discriminator}"
            )

            # Refresh cache
            await TeamPlayerCache.refresh()

            # Get team info for the embed
            teams = await queries.get_all_teams(self.supabase, season['division_id'])
            team = next((t for t in teams if t['id'] == roster_entry['team_id']), None)
            team_display = f"{team['name']} (`{team['code']}`)" if team else "Unknown Team"

            league_display, league_code = self._get_league_display(season)

            # Log to audit trail
            logger.info(
                f"🔧 ADMIN FORCE RELEASE: {player.display_name} from {team_display} "
                f"in {league_display} {season['name']} by {interaction.user.name}"
            )

            # Notify
            files: list[discord.File] = []
            thumbnail: Optional[str] = None
            if team and team.get('code'):
                files, thumbnail = build_team_logo_payload(team['code'])

            embed = discord.Embed(
                title="✅ Admin Force Release",
                color=discord.Color.orange()
            )
            embed.add_field(name="Player", value=player.mention, inline=False)
            embed.add_field(name="Previous Team", value=team_display, inline=False)
            embed.add_field(name="League", value=league_display, inline=False)
            embed.add_field(name="Season", value=season['name'], inline=False)
            embed.add_field(name="Admin", value=interaction.user.mention, inline=False)
            embed.set_footer(text="This was an admin force release (no confirmation needed)")
            if thumbnail:
                target_thumbnail(embed, thumbnail)
            if team:
                apply_team_badge(embed, team.get('code'))
            apply_league_badge(embed, league_code)

            await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

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

        league_display, league_code = self._get_league_display(season)
        apply_league_badge(embed, league_code)

        files: list[discord.File] = []
        thumbnail: Optional[str] = None
        if target_team:
            files, thumbnail = build_team_logo_payload(target_team['code'])
            if thumbnail:
                target_thumbnail(embed, thumbnail)
            apply_team_badge(embed, target_team['code'])

        await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    @app_commands.command(name="trade", description="[TEAM OWNER] Propose a multi-asset trade with another team")
    @app_commands.describe(counterparty_team="Team code (e.g., 'pandas') to trade with")
    async def trade(self, interaction: discord.Interaction, counterparty_team: str):
        """Initiate a trade proposal with multi-asset selection modal."""
        await interaction.response.defer(ephemeral=True)

        # Identify initiator's team
        team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
        if not team:
            await interaction.followup.send(
                "❌ Unable to determine your team. Ensure your Discord ID is set as the team owner.",
                ephemeral=True,
            )
            return

        # Get active season
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

        # Resolve counterparty team
        counterparty = await queries.get_team_by_code(self.supabase, counterparty_team)
        if not counterparty:
            await interaction.followup.send(
                f"❌ Unknown team code `{counterparty_team}`.",
                ephemeral=True,
            )
            return
        if counterparty['division_id'] != team['division_id']:
            await interaction.followup.send(
                "❌ That team is not in your division.",
                ephemeral=True,
            )
            return
        if counterparty['id'] == team['id']:
            await interaction.followup.send(
                "❌ You cannot trade with your own team.",
                ephemeral=True,
            )
            return

        modal = TradeAssetModal(
            cog=self,
            initiating_team=team,
            counterparty_team=counterparty,
            season=season,
            supabase=self.supabase
        )

        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Team Roster
    # ------------------------------------------------------------------
    @app_commands.command(name="roster", description="[MEMBERS] View a team's roster for the active season")
    @app_commands.describe(team_code="Team code (e.g., PANDAS)")
    async def view_roster(self, interaction: discord.Interaction, team_code: str):
        """Display a team's current roster including owner and all active players."""
        await interaction.response.defer(ephemeral=True)

        season = await queries.get_active_season(self.supabase)
        if not season:
            await interaction.followup.send("⚠️ No active season configured.", ephemeral=True)
            return

        # Resolve team by code
        team = await queries.get_team_by_code(self.supabase, team_code)
        if not team:
            await interaction.followup.send(
                f"❌ Unknown team code `{team_code}`.",
                ephemeral=True,
            )
            return

        if team['division_id'] != season['division_id']:
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

        league_display, league_code = self._get_league_display(season)
        files, thumbnail = build_team_logo_payload(team['code'])

        embed = discord.Embed(
            title=f"📋 {team['name']} Roster",
            description=f"Season: **{season['name']}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="League", value=league_display, inline=True)
        embed.add_field(name="Team Code", value=f"`{team['code']}`", inline=True)
        embed.add_field(name="Owner", value=owner_mention, inline=True)

        if roster_lines:
            embed.add_field(
                name=f"Players ({len(roster_lines)})",
                value="\n".join(roster_lines),
                inline=False
            )
        else:
            embed.add_field(name="Players", value="No players on roster yet.", inline=False)

        embed.timestamp = datetime.utcnow()
        if thumbnail:
            target_thumbnail(embed, thumbnail)
        apply_team_badge(embed, team['code'])
        apply_league_badge(embed, league_code)

        await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

    # ------------------------------------------------------------------
    # Transaction history
    # ------------------------------------------------------------------
    @app_commands.command(name="history", description="[MEMBERS] View transaction history with optional filters")
    @app_commands.describe(
        team_code="Optional team code to filter by (e.g., 'pandas')",
        player_name="Optional player name to filter by",
        txn_type="Optional transaction type: sign, release, trade",
        days="Look back this many days (default 30)"
    )
    async def history(
        self,
        interaction: discord.Interaction,
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

        season = await queries.get_active_season(self.supabase)
        if not season:
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

        if not transactions:
            await interaction.followup.send("ℹ️ No transactions match the filters.", ephemeral=True)
            return

        # Load teams and players for display
        teams = await queries.get_all_teams(self.supabase, season['division_id'])
        team_by_id = {team['id']: team for team in teams}

        players = self.supabase.table('players').select('id, display_name').execute().data or []
        player_by_id = {p['id']: p for p in players}

        embed = build_history_embed(transactions, team_by_id, player_by_id)
        _, league_code = self._get_league_display(season)
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

        embed = discord.Embed(title="⚙️ Transactions Configuration", color=discord.Color.blurple())
        if not transactions_cfg:
            embed.description = "No transaction configuration stored for this guild yet."
        else:
            for key, value in transactions_cfg.items():
                embed.add_field(name=key, value=str(value), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(TransactionsCog(bot))
    logger.info("✅ TransactionsCog loaded")
