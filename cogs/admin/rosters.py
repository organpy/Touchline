"""Roster Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, List
from datetime import datetime

import discord
from discord import app_commands

from db import queries
from db import get_supabase
from db.transactions_repository import TransactionsRepository
from cogs.admin.common import LazyInitView
from cogs.admin.base import AdminBaseManager
from utils.team_player_cache import TeamPlayerCache
from cogs.score.roster_cache import RosterCache
from utils.config_manager import get_config_manager
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)
from utils.team_colors import get_team_color
from db.db_utils import execute_async

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class RosterActionsView(discord.ui.View):
    """Buttons for roster management actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.original_message: Optional[discord.Message] = None
        self.repo = TransactionsRepository(supabase)

    def create_embed(self) -> discord.Embed:
        """Build dark minimalistic roster management embed."""
        embed = discord.Embed(
            title="Roster Management",
            description="Force sign, release, and transfer players.",
            color=0x2B2D31,  # Dark theme
        )
        embed.add_field(
            name="Actions",
            value=(
                "**Sign** - Add player to roster\n"
                "**Release** - Remove from roster\n"
                "**Transfer** - Move between teams\n"
                "**Block** - Manage blacklists\n"
                "**View** - List roster"
            ),
            inline=False,
        )
        return embed

    async def update_display(self) -> None:
        """Refresh the embed message."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update roster management embed: %s", exc)

    @discord.ui.button(label="Sign", style=discord.ButtonStyle.secondary, row=0)
    async def force_sign(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ForceSignView(self.bot, self.guild_id, self.supabase, self.repo, self)
        await view.prepare()
        embed = discord.Embed(
            title="✍️ Force Sign Player",
            description="Select team and Discord member to force sign.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Release", style=discord.ButtonStyle.secondary, row=0)
    async def force_release(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ForceReleaseView(self.supabase, self, self.repo)
        await view.prepare()
        embed = discord.Embed(
            title="🚪 Force Release Player",
            description="Select player to release from their current team.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, row=0)
    async def force_transfer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ForceTransferView(self.supabase, self, self.repo)
        await view.prepare()
        embed = discord.Embed(
            title="🔄 Force Transfer Player",
            description="Select player and destination team.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Block", style=discord.ButtonStyle.danger, row=0)
    async def blacklist(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = BlacklistView(self.bot, self.guild_id, self.supabase, self)
        await view.prepare()
        embed = discord.Embed(
            title="🚫 Blacklist Management",
            description="Add or remove players from team blacklists.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="View", style=discord.ButtonStyle.secondary, row=1)
    async def view_roster(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ViewRosterView(self.supabase, self)
        await view.prepare()
        embed = discord.Embed(
            title="📋 View Team Roster",
            description="Select team to view active roster.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to main dashboard."""
        await interaction.response.defer()
        self.stop()


class ForceSignView(discord.ui.View):
    """Simple force sign: Select team → Select Discord member → Confirm sign."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any, repo: TransactionsRepository, parent_view: RosterActionsView) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.repo = repo
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self._selected_team: Optional[Dict[str, Any]] = None
        self._selected_member: Optional[discord.Member] = None
        self._teams: List[Dict[str, Any]] = []
        self.manager = AdminBaseManager()
        
        # Clear existing items and add team select
        self.clear_items()

    async def prepare(self) -> None:
        """Load and display team options."""
        try:
            self._teams = await self.manager.get_teams_for_guild(self.guild_id)
        except Exception as exc:
            logger.error("Failed to load teams: %s", exc)
            self._teams = []

        if not self._teams:
            self.add_item(
                discord.ui.Select(
                    placeholder="No teams configured.",
                    options=[discord.SelectOption(label="No teams available", value="none")],
                    disabled=True,
                )
            )
            return

        team_select = discord.ui.Select(
            placeholder="Select team...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{t.get('name', 'Unknown')} ({t.get('code', '?')})",
                    value=t.get('id', ''),
                )
                for t in sorted(self._teams, key=lambda x: x.get('code', ''))
            ],
            row=0,
        )
        team_select.callback = self.select_team
        self.add_item(team_select)

    async def select_team(self, interaction: discord.Interaction) -> None:
        """Handle team selection."""
        await interaction.response.defer(ephemeral=True)

        team_id = interaction.data['values'][0]
        self._selected_team = next((t for t in self._teams if t.get('id') == team_id), None)

        if not self._selected_team:
            await interaction.followup.send("❌ Team not found.", ephemeral=True)
            return

        # Get guild members for member selection
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                await interaction.followup.send("❌ Guild not found.", ephemeral=True)
                return

            # Build member select (max 25 members)
            members = sorted(guild.members, key=lambda m: m.display_name)
            display_count = 24 if len(members) > 25 else 25
            member_options = [
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=f"ID: {m.id}",
                )
                for m in members[:display_count]
            ]

            if len(members) > 25:
                member_options.append(
                    discord.SelectOption(
                        label=f"... and {len(members) - 24} more members",
                        value="__more__",
                    )
                )

            member_select = discord.ui.Select(
                placeholder="Select Discord member...",
                min_values=1,
                max_values=1,
                options=member_options,
                row=0,
            )
            member_select.callback = self.select_member
            
            view = discord.ui.View()
            view.add_item(member_select)

            await interaction.followup.send(
                f"Team: **{self._selected_team.get('name')}** (`{self._selected_team.get('code')}`)\n\n"
                f"Select Discord member to sign:",
                view=view,
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Failed to fetch guild members: %s", exc)
            await interaction.followup.send("❌ Failed to fetch guild members.", ephemeral=True)

    async def select_member(self, interaction: discord.Interaction) -> None:
        """Handle member selection and show confirmation."""
        await interaction.response.defer(ephemeral=True)

        member_id_str = interaction.data['values'][0]
        if member_id_str == "__more__":
            await interaction.followup.send(
                "⚠️ Too many members to display. Please have the player provide their Discord ID or try /link-discord.",
                ephemeral=True,
            )
            return

        try:
            member_id = int(member_id_str)
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                await interaction.followup.send("❌ Guild not found.", ephemeral=True)
                return

            self._selected_member = guild.get_member(member_id)
            if not self._selected_member:
                await interaction.followup.send("❌ Member not found in guild.", ephemeral=True)
                return

            # Show confirmation
            from utils.team_colors import get_team_color
            team_color = get_team_color(self._selected_team.get('code'))
            embed = discord.Embed(
                title="✍️ Confirm Force Sign",
                color=team_color,
            )
            embed.add_field(name="Team", value=f"**{self._selected_team.get('name')}**", inline=False)
            embed.add_field(name="Member", value=f"**{self._selected_member.display_name}** (<@{member_id}>)", inline=False)

            confirm_view = discord.ui.View()
            confirm_view.add_item(
                discord.ui.Button(
                    label="Confirm Sign",
                    style=discord.ButtonStyle.success,
                    custom_id="confirm_force_sign",
                )
            )
            confirm_view.add_item(
                discord.ui.Button(
                    label="Cancel",
                    style=discord.ButtonStyle.danger,
                    custom_id="cancel_force_sign",
                )
            )

            async def confirm_callback(btn_interaction: discord.Interaction) -> None:
                await self.confirm_force_sign(btn_interaction)

            async def cancel_callback(btn_interaction: discord.Interaction) -> None:
                await btn_interaction.response.defer(ephemeral=True)
                await btn_interaction.followup.send("❌ Force sign cancelled.", ephemeral=True)

            confirm_view.children[0].callback = confirm_callback
            confirm_view.children[1].callback = cancel_callback

            await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Invalid member ID.", ephemeral=True)
        except Exception as exc:
            logger.error("Error in select_member: %s", exc)
            await interaction.followup.send("❌ Unexpected error occurred.", ephemeral=True)

    async def confirm_force_sign(self, interaction: discord.Interaction) -> None:
        """Handle force sign confirmation."""
        await interaction.response.defer(ephemeral=True)

        if not self._selected_team or not self._selected_member:
            await interaction.followup.send("❌ Selection lost. Please try again.", ephemeral=True)
            return

        try:
            # Get or create player for this Discord ID
            player = await queries.get_player_by_discord_id(self.supabase, self._selected_member.id)
            if not player:
                # Create new player record with Discord ID
                result = await execute_async(
                    self.supabase
                    .table('players')
                    .insert({
                        'display_name': self._selected_member.display_name,
                        'discord_id': self._selected_member.id,
                    })
                )
                if result.data:
                    player = result.data[0]
                else:
                    await interaction.followup.send("❌ Failed to create player record.", ephemeral=True)
                    return

            # Get active season for team's division
            team_division_id = self._selected_team.get('division_id')
            season_response = await execute_async(
                self.supabase
                .table('seasons')
                .select('*')
                .eq('division_id', team_division_id)
                .eq('active', True)
                .limit(1)
            )
            season = season_response.data[0] if season_response.data else None
            if not season:
                await interaction.followup.send(
                    f"❌ No active season found for division {team_division_id}. Cannot sign player.",
                    ephemeral=True,
                )
                return

            # Check if already on roster
            existing_roster = await execute_async(
                self.supabase
                .table('rosters')
                .select('*')
                .eq('season_id', season['id'])
                .eq('team_id', self._selected_team['id'])
                .eq('player_id', player['id'])
                .is_('left_at', None)
            )
            if existing_roster.data:
                await interaction.followup.send(
                    f"⚠️ **{player['display_name']}** is already on the roster.",
                    ephemeral=True,
                )
                return

            # Sign player
            transaction_result = await self.repo.insert_transaction({
                'season_id': season['id'],
                'transaction_type': 'admin_force_sign',
                'player_id': player['id'],
                'team_id': self._selected_team['id'],
                'metadata': {
                    'from_team_id': None,
                    'admin_user_id': interaction.user.id,
                    'signed_by': interaction.user.display_name,
                },
            })
            transaction_id = transaction_result.get('id') if transaction_result else None

            if transaction_id:
                # Import unified embed utilities
                from utils.embed_builder import EmbedBuilder
                from utils.team_display import format_team_label_from_record
                from utils.team_colors import get_division_color
                
                # Get league code from division
                league_code = ''
                division_id = self._selected_team.get('division_id')
                if division_id:
                    try:
                        div_resp = await execute_async(self.supabase.table('divisions').select('league_type').eq('id', division_id).limit(1))
                        if div_resp.data:
                            league_code = div_resp.data[0].get('league_type', '').upper()
                    except Exception:
                        pass
                
                team_code = self._selected_team.get('code', '')
                team_label = format_team_label_from_record(self._selected_team)
                
                # Post to transactions channel
                config_mgr = get_config_manager()
                tx_config = await config_mgr.get_transactions_config(self.guild_id)
                channel_id = tx_config.get('channel_id')

                if channel_id:
                    try:
                        channel = self.bot.get_channel(int(channel_id))
                        if channel and isinstance(channel, discord.TextChannel):
                            # Build unified embed
                            builder = EmbedBuilder(
                                title="✍️ Admin Force Sign",
                                description=f"<@{self._selected_member.id}> has been signed to {team_label}",
                                team_code=team_code,
                                division_code=league_code,
                            )
                            builder.add_league_field(league_code)
                            builder.add_field("Season", season.get('name'), inline=True)
                            builder.add_field("Player", self._selected_member.mention, inline=True)
                            builder.add_field("Team", team_label, inline=True)
                            builder.add_field("Executed by", interaction.user.mention, inline=False)
                            builder.add_timestamp()
                            builder.set_footer_text("This was an admin force sign (no confirmation needed)")
                            builder.add_team_image(team_code, position="thumbnail")
                            
                            embed, files = builder.build_with_files()
                            # Ping the player
                            await channel.send(
                                content=f"<@{self._selected_member.id}>",
                                embed=embed,
                                files=files or None,
                                allowed_mentions=discord.AllowedMentions(users=[self._selected_member]),
                            )
                    except Exception as channel_error:
                        logger.warning("Failed to post to transaction channel: %s", channel_error)

                await TeamPlayerCache.refresh()

                # Confirmation embed to admin
                confirm_builder = EmbedBuilder(
                    title="✅ Player Signed",
                    description=f"**{player['display_name']}** has been signed to {team_label} for **{season.get('name')}**",
                    team_code=team_code,
                    division_code=league_code,
                )
                confirm_embed = confirm_builder.build()
                await interaction.followup.send(embed=confirm_embed, ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to create transaction.", ephemeral=True)

        except Exception as exc:
            logger.error("Error confirming force sign: %s", exc, exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(exc)[:100]}", ephemeral=True)


class ForceReleaseView(LazyInitView):
    """Select active player and release."""

    def __init__(self, supabase: Any, parent_view: RosterActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None

    async def populate(self) -> None:
        """Load active roster players."""
        try:
            active_rosters = await execute_async(self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, joined_at, '
                'players(id, display_name), '
                'teams(code, name)'
            ).is_('left_at', 'null').order('joined_at', desc=True).limit(100))
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load roster entries for release: %s", exc)
            active_rosters = None

        if not active_rosters or not active_rosters.data:
            empty_select = discord.ui.Select(
                placeholder="No active roster entries",
                options=[discord.SelectOption(label="No players found", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        player_select = discord.ui.Select(
            placeholder="Select player to release...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{r['players'].get('display_name', 'Unknown')} - {r['teams'].get('code', '?')}",
                    value=r.get('id', ''),
                    description=r.get('team_id', ''),
                )
                for r in active_rosters.data[:25]
            ],
            row=0,
        )
        player_select.callback = self.select_player_to_release
        self.add_item(player_select)

    async def select_player_to_release(self, interaction: discord.Interaction) -> None:
        """Handle player selection for release."""
        await interaction.response.defer(ephemeral=True)
        roster_id = interaction.data['values'][0]

        try:
            roster = await execute_async(self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name, discord_id), teams(name, code)'
            ).eq('id', roster_id).limit(1))

            if roster.data:
                roster_entry = roster.data[0]
                player_name = roster_entry['players'].get('display_name', 'Unknown')
                player_discord_id = roster_entry['players'].get('discord_id')
                team_code = roster_entry['teams'].get('code', '?')
                team_name = roster_entry['teams'].get('name', 'Unknown')

                # Confirmation
                class ConfirmReleaseView(discord.ui.View):
                    def __init__(self) -> None:
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Confirm Release", style=discord.ButtonStyle.danger, emoji="🚪")
                    async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await confirm_interaction.response.defer(ephemeral=True)
                        try:
                            # Insert transaction log; the trg_transactions_apply_roster trigger closes
                            # the roster row for from_team_id.
                            transaction = await self.repo.insert_transaction({
                                'season_id': roster_entry['season_id'],
                                'transaction_type': 'admin_force_release',
                                'player_id': roster_entry['player_id'],
                                'metadata': {
                                    'from_team_id': str(roster_entry['team_id']),
                                    'admin_user_id': str(confirm_interaction.user.id),
                                    'reason': 'admin_force_release',
                                },
                            })
                            if not transaction:
                                await confirm_interaction.followup.send(
                                    "❌ Failed to release player. Check server logs.",
                                    ephemeral=True,
                                )
                                return
                            logger.info(
                                "✅ Force release: %s from %s by %s",
                                player_name,
                                team_code,
                                confirm_interaction.user.id,
                            )
                            RosterCache().invalidate()
                            await TeamPlayerCache.refresh(self.supabase)
                            
                            # Post to transactions channel
                            config_mgr = get_config_manager()
                            tx_config = await config_mgr.get_transactions_config(self.parent_view.guild_id)
                            channel_id = tx_config.get('channel_id')
                            if channel_id:
                                try:
                                    channel = self.parent_view.bot.get_channel(int(channel_id))
                                    if channel and isinstance(channel, discord.TextChannel):
                                        # Get team and league info for styling
                                        from utils.embed_builder import EmbedBuilder
                                        from utils.team_display import format_team_label_by_code
                                        
                                        cache = TeamPlayerCache()
                                        team_info = cache.get_team_by_code(team_code) or {}
                                        league_code = team_info.get('league_type', '').upper()
                                        team_label = format_team_label_by_code(team_code)
                                        
                                        # Build unified embed
                                        player_mention = f"<@{player_discord_id}>" if player_discord_id else player_name
                                        builder = EmbedBuilder(
                                            title="🚪 Admin Force Release",
                                            description=f"{player_mention} has been released from {team_label}",
                                            team_code=team_code,
                                            division_code=league_code,
                                        )
                                        builder.add_league_field(league_code)
                                        builder.add_field("Player", player_mention, inline=True)
                                        builder.add_field("Team", team_label, inline=True)
                                        builder.add_field("Executed by", confirm_interaction.user.mention, inline=False)
                                        builder.add_timestamp()
                                        builder.set_footer_text("This was an admin force release (no confirmation needed)")
                                        builder.add_team_image(team_code, position="thumbnail")
                                        
                                        embed, files = builder.build_with_files()
                                        # Ping the player
                                        ping_content = f"<@{player_discord_id}>" if player_discord_id else None
                                        player_member = confirm_interaction.guild.get_member(int(player_discord_id)) if player_discord_id else None
                                        await channel.send(
                                            content=ping_content,
                                            embed=embed,
                                            files=files or None,
                                            allowed_mentions=discord.AllowedMentions(users=[player_member] if player_member else True),
                                        )
                                except Exception as e:
                                    logger.warning(f"Failed to post admin release to transactions channel: {e}")
                            
                            await confirm_interaction.followup.send(
                                f"✅ **{player_name}** released from **{team_code}**",
                                ephemeral=True,
                            )
                            await self.parent_view.update_display()
                        except Exception as exc:  # pragma: no cover
                            logger.error("Failed to force release player: %s", exc)
                            await confirm_interaction.followup.send(
                                "❌ Failed to release player. Check server logs.",
                                ephemeral=True,
                            )

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await cancel_interaction.response.defer(ephemeral=True)
                        await cancel_interaction.followup.send("❌ Cancelled.", ephemeral=True)

                confirm_view = ConfirmReleaseView()
                confirm_view.supabase = self.supabase
                confirm_view.parent_view = self.parent_view
                confirm_view.repo = self.repo

                from utils.team_colors import get_team_color
                team_color = get_team_color(team_code)
                embed = discord.Embed(
                    title="🚪 Confirm Force Release",
                    description=f"Release **{player_name}** from **{team_name}**?",
                    color=team_color,
                )
                await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch roster entry: %s", exc)
            await interaction.followup.send("❌ Failed to fetch roster entry.", ephemeral=True)



class ForceTransferView(LazyInitView):
    """Transfer player between teams."""

    def __init__(self, supabase: Any, parent_view: RosterActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None
        self._selected_roster: Optional[Dict[str, Any]] = None
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load active roster players."""
        try:
            active_rosters = await execute_async(self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name, discord_id), teams(code, name)'
            ).is_('left_at', 'null').order('joined_at', desc=True).limit(100))
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load roster entries for transfer: %s", exc)
            active_rosters = None

        if not active_rosters or not active_rosters.data:
            empty_select = discord.ui.Select(
                placeholder="No active roster entries",
                options=[discord.SelectOption(label="No players found", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        player_select = discord.ui.Select(
            placeholder="Select player to transfer...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{r['players'].get('display_name', 'Unknown')} - {r['teams'].get('code', '?')}",
                    value=r.get('id', ''),
                )
                for r in active_rosters.data[:25]
            ],
            row=0,
        )
        player_select.callback = self.select_player_to_transfer
        self.add_item(player_select)

    async def select_player_to_transfer(self, interaction: discord.Interaction) -> None:
        """Handle player selection for transfer."""
        await interaction.response.defer(ephemeral=True)
        roster_id = interaction.data['values'][0]

        try:
            roster = await execute_async(self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name, discord_id), teams(name, code)'
            ).eq('id', roster_id).limit(1))

            if roster.data:
                self._selected_roster = roster.data[0]
                player_name = self._selected_roster['players'].get('display_name', 'Unknown')
                current_team_code = self._selected_roster['teams'].get('code', '?')

                # ✅ FIXED: Use manager to get teams scoped to guild
                teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
                if not teams:
                    await interaction.followup.send("❌ No teams found.", ephemeral=True)
                    return

                # Filter out current team
                available_teams = [t for t in teams if t.get('id') != self._selected_roster['team_id']]
                if not available_teams:
                    await interaction.followup.send("❌ No other teams available.", ephemeral=True)
                    return

                team_select = discord.ui.Select(
                    placeholder="Select destination team...",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(
                            label=t.get('name', 'Unknown')[:100],
                            value=t.get('id', ''),
                            description=t.get('code', '?'),
                        )
                        for t in sorted(available_teams, key=lambda x: x.get('code', ''))[:25]
                    ],
                    row=0,
                )
                team_select.callback = self.select_destination_team
                view = discord.ui.View()
                view.add_item(team_select)

                await interaction.followup.send(
                    f"Player: **{player_name}**\n"
                    f"Current Team: **{current_team_code}**\n\n"
                    f"Select destination team:",
                    view=view,
                    ephemeral=True,
                )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch roster entry: %s", exc)
            await interaction.followup.send("❌ Failed to fetch roster entry.", ephemeral=True)

    async def select_destination_team(self, interaction: discord.Interaction) -> None:
        """Handle destination team selection."""
        await interaction.response.defer(ephemeral=True)
        destination_team_id = interaction.data['values'][0]

        try:
            dest_team = await execute_async(self.supabase.table('teams').select('*').eq('id', destination_team_id).limit(1))
            if dest_team.data:
                dest_team_data = dest_team.data[0]
                player_name = self._selected_roster['players'].get('display_name', 'Unknown')
                player_discord_id = self._selected_roster['players'].get('discord_id')
                current_code = self._selected_roster['teams'].get('code', '?')
                dest_code = dest_team_data.get('code', '?')

                # Confirmation
                class ConfirmTransferView(discord.ui.View):
                    def __init__(self) -> None:
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Confirm Transfer", style=discord.ButtonStyle.success, emoji="✅")
                    async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await confirm_interaction.response.defer(ephemeral=True)
                        try:
                            # Insert transaction log; the trg_transactions_apply_roster trigger moves the
                            # roster row, so the roster must not also be updated here.
                            transaction = await self.repo.insert_transaction({
                                'season_id': self._selected_roster['season_id'],
                                'transaction_type': 'admin_force_transfer',
                                'player_id': self._selected_roster['player_id'],
                                'team_id': destination_team_id,
                                'metadata': {
                                    'from_team_id': str(self._selected_roster['team_id']),
                                    'admin_user_id': str(confirm_interaction.user.id),
                                    'reason': 'admin_force_transfer',
                                },
                            })
                            if not transaction:
                                await confirm_interaction.followup.send(
                                    "❌ Failed to transfer player. Check server logs.",
                                    ephemeral=True,
                                )
                                return
                            logger.info(
                                "✅ Force transfer: %s from %s to %s by %s",
                                player_name,
                                current_code,
                                dest_code,
                                confirm_interaction.user.id,
                            )
                            RosterCache().invalidate()
                            await TeamPlayerCache.refresh(self.supabase)
                            
                            # Post to transactions channel
                            config_mgr = get_config_manager()
                            tx_config = await config_mgr.get_transactions_config(self.parent_view.guild_id)
                            channel_id = tx_config.get('channel_id')
                            if channel_id:
                                try:
                                    channel = self.parent_view.bot.get_channel(int(channel_id))
                                    if channel and isinstance(channel, discord.TextChannel):
                                        # Get team info for styling
                                        from utils.embed_builder import EmbedBuilder
                                        from utils.team_display import format_team_label_by_code
                                        
                                        cache = TeamPlayerCache()
                                        source_team = cache.get_team_by_code(current_code) or {}
                                        dest_team_info = cache.get_team_by_code(dest_code) or {}
                                        league_code = dest_team_info.get('league_type', '').upper()
                                        
                                        from_team_label = format_team_label_by_code(current_code)
                                        to_team_label = format_team_label_by_code(dest_code)
                                        
                                        # Build unified embed
                                        player_mention = f"<@{player_discord_id}>" if player_discord_id else player_name
                                        builder = EmbedBuilder(
                                            title="🔄 Admin Force Transfer",
                                            description=f"{player_mention} has been transferred from {from_team_label} to {to_team_label}",
                                            team_code=dest_code,
                                            division_code=league_code,
                                        )
                                        builder.add_league_field(league_code)
                                        builder.add_field("Player", player_mention, inline=True)
                                        builder.add_field("From Team", from_team_label, inline=True)
                                        builder.add_field("To Team", to_team_label, inline=True)
                                        builder.add_field("Executed by", confirm_interaction.user.mention, inline=False)
                                        builder.add_timestamp()
                                        builder.set_footer_text("This was an admin force transfer (no confirmation needed)")
                                        builder.add_team_image(dest_code, position="thumbnail")
                                        
                                        embed, files = builder.build_with_files()
                                        # Ping the player
                                        ping_content = f"<@{player_discord_id}>" if player_discord_id else None
                                        player_member = confirm_interaction.guild.get_member(int(player_discord_id)) if player_discord_id else None
                                        await channel.send(
                                            content=ping_content,
                                            embed=embed,
                                            files=files or None,
                                            allowed_mentions=discord.AllowedMentions(users=[player_member] if player_member else True),
                                        )
                                except Exception as e:
                                    logger.warning(f"Failed to post admin transfer to transactions channel: {e}")
                            
                            await confirm_interaction.followup.send(
                                f"✅ **{player_name}** transferred from **{current_code}** to **{dest_code}**",
                                ephemeral=True,
                            )
                            await self.parent_view.update_display()
                        except Exception as exc:  # pragma: no cover
                            logger.error("Failed to transfer player: %s", exc)
                            await confirm_interaction.followup.send(
                                "❌ Failed to transfer player. Check server logs.",
                                ephemeral=True,
                            )

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await cancel_interaction.response.defer(ephemeral=True)
                        await cancel_interaction.followup.send("❌ Cancelled.", ephemeral=True)

                confirm_view = ConfirmTransferView()
                confirm_view.supabase = self.supabase
                confirm_view.parent_view = self.parent_view
                confirm_view.repo = self.repo
                confirm_view._selected_roster = self._selected_roster

                from utils.team_colors import get_team_color
                team_color = get_team_color(dest_code)
                embed = discord.Embed(
                    title="✅ Confirm Force Transfer",
                    description=(
                        f"**Player**: {player_name}\n"
                        f"**From**: {current_code}\n"
                        f"**To**: {dest_code}\n\n"
                        f"Confirm transfer?"
                    ),
                    color=team_color,
                )
                await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch destination team: %s", exc)
            await interaction.followup.send("❌ Failed to fetch team.", ephemeral=True)



class BlacklistView(discord.ui.View):
    """Blacklist management: Add/Remove players from team blacklists."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any, parent_view: RosterActionsView) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self._selected_team: Optional[Dict[str, Any]] = None
        self._teams: List[Dict[str, Any]] = []
        self.manager = AdminBaseManager()

    async def prepare(self) -> None:
        """Load and display team options."""
        try:
            self._teams = await self.manager.get_teams_for_guild(self.guild_id)
        except Exception as exc:
            logger.error("Failed to load teams for blacklist: %s", exc)
            self._teams = []

        if not self._teams:
            self.add_item(
                discord.ui.Select(
                    placeholder="No teams configured.",
                    options=[discord.SelectOption(label="No teams available", value="none")],
                    disabled=True,
                )
            )
            return

        team_select = discord.ui.Select(
            placeholder="Select team...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{t.get('name', 'Unknown')} ({t.get('code', '?')})",
                    value=t.get('id', ''),
                )
                for t in sorted(self._teams, key=lambda x: x.get('code', ''))
            ],
            row=0,
        )
        team_select.callback = self.select_team
        self.add_item(team_select)

    async def select_team(self, interaction: discord.Interaction) -> None:
        """Handle team selection and show blacklist actions."""
        await interaction.response.defer(ephemeral=True)

        team_id = interaction.data['values'][0]
        self._selected_team = next((t for t in self._teams if t.get('id') == team_id), None)

        if not self._selected_team:
            await interaction.followup.send("❌ Team not found.", ephemeral=True)
            return

        # Build action buttons
        action_view = discord.ui.View()
        
        add_btn = discord.ui.Button(
            label="Add to Blacklist",
            style=discord.ButtonStyle.danger,
            custom_id="add_blacklist",
        )
        
        remove_btn = discord.ui.Button(
            label="Remove from Blacklist",
            style=discord.ButtonStyle.secondary,
            custom_id="remove_blacklist",
        )

        async def add_callback(btn_interaction: discord.Interaction) -> None:
            await self.show_player_select(btn_interaction, "add")

        async def remove_callback(btn_interaction: discord.Interaction) -> None:
            await self.show_player_select(btn_interaction, "remove")

        add_btn.callback = add_callback
        remove_btn.callback = remove_callback
        action_view.add_item(add_btn)
        action_view.add_item(remove_btn)

        embed = discord.Embed(
            title="🚫 Blacklist Management",
            description=f"Team: **{self._selected_team.get('name')}** (`{self._selected_team.get('code')}`)",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Options",
            value="• **Add to Blacklist** - Prevent player from joining\n• **Remove from Blacklist** - Allow player again",
            inline=False,
        )

        await interaction.followup.send(embed=embed, view=action_view, ephemeral=True)

    async def show_player_select(self, interaction: discord.Interaction, action: str) -> None:
        """Show player dropdown for add/remove blacklist action."""
        await interaction.response.defer(ephemeral=True)

        if not self._selected_team:
            await interaction.followup.send("❌ Team selection lost. Please try again.", ephemeral=True)
            return

        try:
            # Get guild members for selection
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                await interaction.followup.send("❌ Guild not found.", ephemeral=True)
                return

            members = sorted(guild.members, key=lambda m: m.display_name)
            display_count = 24 if len(members) > 25 else 25
            member_options = [
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=f"ID: {m.id}",
                )
                for m in members[:display_count]
            ]

            if len(members) > 25:
                member_options.append(
                    discord.SelectOption(
                        label=f"... and {len(members) - 24} more members",
                        value="__more__",
                    )
                )

            member_select = discord.ui.Select(
                placeholder=f"Select Discord member to {action}...",
                min_values=1,
                max_values=1,
                options=member_options,
                row=0,
            )

            async def select_member_callback(member_interaction: discord.Interaction) -> None:
                member_id_str = member_interaction.data['values'][0]
                if member_id_str == "__more__":
                    await member_interaction.response.defer(ephemeral=True)
                    await member_interaction.followup.send(
                        "⚠️ Too many members to display. Please provide Discord ID or try another approach.",
                        ephemeral=True,
                    )
                    return

                try:
                    member_id = int(member_id_str)
                    member = guild.get_member(member_id)
                    if not member:
                        await member_interaction.response.defer(ephemeral=True)
                        await member_interaction.followup.send("❌ Member not found.", ephemeral=True)
                        return

                    await member_interaction.response.defer(ephemeral=True)
                    await self.confirm_blacklist_action(member_interaction, action, member)
                except ValueError:
                    await member_interaction.response.defer(ephemeral=True)
                    await member_interaction.followup.send("❌ Invalid member ID.", ephemeral=True)

            member_select.callback = select_member_callback
            view = discord.ui.View()
            view.add_item(member_select)

            action_text = "add to blacklist" if action == "add" else "remove from blacklist"
            await interaction.followup.send(
                f"Select Discord member to {action_text}:",
                view=view,
                ephemeral=True,
            )
        except Exception as exc:
            logger.error("Error showing player select: %s", exc)
            await interaction.followup.send("❌ Failed to fetch members.", ephemeral=True)

    async def confirm_blacklist_action(
        self, interaction: discord.Interaction, action: str, member: discord.Member
    ) -> None:
        """Confirm and execute blacklist action."""
        try:
            # Get or create player record
            player = await queries.get_player_by_discord_id(self.supabase, member.id)
            if not player:
                # Create new player record
                result = await execute_async(
                    self.supabase
                    .table('players')
                    .insert({
                        'display_name': member.display_name,
                        'discord_id': member.id,
                        'active': True,
                    })
                )
                if result.data:
                    player = result.data[0]
                else:
                    await interaction.followup.send("❌ Failed to create player record.", ephemeral=True)
                    return

            team_id = self._selected_team['id']
            team_name = self._selected_team.get('name', 'Unknown')

            # Get blacklist data (stored in teams table as JSON metadata)
            team_data = await execute_async(self.supabase.table('teams').select('metadata').eq('id', team_id).limit(1))
            metadata = {}
            if team_data.data and team_data.data[0].get('metadata'):
                metadata = team_data.data[0]['metadata']

            if 'blacklist' not in metadata:
                metadata['blacklist'] = []

            blacklist = metadata['blacklist']

            if action == "add":
                if player['id'] not in blacklist:
                    blacklist.append(player['id'])
                    message = f"✅ **{member.display_name}** added to **{team_name}** blacklist"
                else:
                    await interaction.followup.send(f"⚠️ **{member.display_name}** is already blacklisted.", ephemeral=True)
                    return
            else:  # remove
                if player['id'] in blacklist:
                    blacklist.remove(player['id'])
                    message = f"✅ **{member.display_name}** removed from **{team_name}** blacklist"
                else:
                    await interaction.followup.send(f"⚠️ **{member.display_name}** is not blacklisted.", ephemeral=True)
                    return

            # Update team metadata
            metadata['blacklist'] = blacklist
            await execute_async(self.supabase.table('teams').update({'metadata': metadata}).eq('id', team_id))

            embed = discord.Embed(
                title="🚫 Blacklist Updated",
                description=message,
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as exc:
            logger.error("Error in confirm_blacklist_action: %s", exc, exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(exc)[:100]}", ephemeral=True)


class ViewRosterView(LazyInitView):
    """View active roster for a team."""

    def __init__(self, supabase: Any, parent_view: RosterActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load teams."""
        try:
            # ✅ FIXED: Use manager to get teams scoped to guild
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load teams for roster view: %s", exc)
            teams = None

        if not teams:
            empty_select = discord.ui.Select(
                placeholder="No teams available",
                options=[discord.SelectOption(label="No teams found", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        team_select = discord.ui.Select(
            placeholder="Select team to view roster...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=t.get('name', 'Unknown')[:100],
                    value=t.get('id', ''),
                    description=t.get('code', '?'),
                )
                for t in sorted(teams, key=lambda x: x.get('code', ''))[:25]
            ],
            row=0,
        )
        team_select.callback = self.view_team_roster
        self.add_item(team_select)

    async def view_team_roster(self, interaction: discord.Interaction) -> None:
        """Show roster for team."""
        await interaction.response.defer(ephemeral=True)
        team_id = interaction.data['values'][0]

        try:
            active_rosters = await execute_async(self.supabase.table('rosters').select(
                'id, player_id, season_id, joined_at, players(display_name, discord_id), seasons(name)'
            ).eq('team_id', team_id).is_('left_at', 'null').order('joined_at', desc=False))

            team = await execute_async(self.supabase.table('teams').select('*').eq('id', team_id).limit(1))
            if not team.data:
                await interaction.followup.send("❌ Team not found.", ephemeral=True)
                return

            team_data = team.data[0]
            embed = discord.Embed(
                title=f"📋 {team_data.get('name', 'Unknown')} Roster",
                description=f"Code: `{team_data.get('code', '?')}`",
                color=discord.Color.blue(),
            )

            if not active_rosters.data:
                embed.add_field(name="Players", value="No active roster entries.", inline=False)
            else:
                # Group by season
                by_season: Dict[str, list] = {}
                for roster in active_rosters.data:
                    season_name = roster.get('seasons', {}).get('name', 'Unknown')
                    if season_name not in by_season:
                        by_season[season_name] = []
                    by_season[season_name].append(roster)

                for season_name, rosters in by_season.items():
                    player_lines = [
                        f"• {r['players'].get('display_name', 'Unknown')}"
                        for r in rosters
                    ]
                    embed.add_field(
                        name=f"Season: {season_name}",
                        value="\n".join(player_lines),
                        inline=False,
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch roster: %s", exc)
            await interaction.followup.send("❌ Failed to fetch roster.", ephemeral=True)

