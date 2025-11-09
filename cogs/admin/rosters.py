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
        """Build roster management embed."""
        embed = discord.Embed(
            title="🏀 Roster Management",
            description="Force sign, release, transfer players, and manage blacklists.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• ✍️ **Force Sign** - Add player to roster immediately\n"
                "• 🚪 **Force Release** - Remove player from roster\n"
                "• 🔄 **Force Transfer** - Move player between teams\n"
                "• 🚫 **Blacklist** - Prevent player from joining team\n"
                "• 📋 **View Roster** - List active players on team"
            ),
            inline=False,
        )
        embed.set_footer(text="Click a button below.")
        return embed

    async def update_display(self) -> None:
        """Refresh the embed message."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update roster management embed: %s", exc)

    @discord.ui.button(label="Force Sign", emoji="✍️", style=discord.ButtonStyle.primary, row=0)
    async def force_sign(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ForceSignView(self.supabase, self, self.repo)
        await view.prepare()
        embed = discord.Embed(
            title="✍️ Force Sign Player",
            description="Select season, team, and player to force sign.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Force Release", emoji="🚪", style=discord.ButtonStyle.primary, row=0)
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

    @discord.ui.button(label="Force Transfer", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
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

    @discord.ui.button(label="Blacklist", emoji="🚫", style=discord.ButtonStyle.danger, row=0)
    async def blacklist(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "🚫 Blacklist Management - Coming soon!",
            ephemeral=True,
        )

    @discord.ui.button(label="View Roster", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
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

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to main dashboard."""
        await interaction.response.defer()
        self.stop()


class ForceSignView(LazyInitView):
    """Multi-step view: Division → Season → Team → Player → Confirm."""

    def __init__(self, supabase: Any, parent_view: RosterActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None
        self._selected_division: Optional[Dict[str, Any]] = None
        self._selected_season: Optional[Dict[str, Any]] = None
        self._selected_team: Optional[Dict[str, Any]] = None
        self._selected_player: Optional[Dict[str, Any]] = None
        self._divisions: List[Dict[str, Any]] = []
        self.manager = AdminBaseManager()

    async def populate(self) -> None:
        """Load available divisions linked to this guild."""
        try:
            self._divisions = await self.manager.get_guild_divisions(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load divisions for force sign: %s", exc)
            self._divisions = []

        if not self._divisions:
            empty_select = discord.ui.Select(
                placeholder="No divisions configured. Use /admin-config to link divisions first.",
                options=[discord.SelectOption(label="No divisions available", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        division_select = discord.ui.Select(
            placeholder="Select division...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(division.get('name') or division.get('league_type', 'Unknown'))[:100],
                    value=division.get('id', ''),
                    description=f"League: {(division.get('league_type') or 'N/A').upper()}",
                )
                for division in sorted(
                    self._divisions,
                    key=lambda d: (
                        (d.get('league_type') or '').upper(),
                        d.get('name') or '',
                    ),
                )
            ],
            row=0,
        )
        division_select.callback = self.select_division
        self.add_item(division_select)

    async def select_division(self, interaction: discord.Interaction) -> None:
        """Handle division selection before seasons."""
        await interaction.response.defer(ephemeral=True)

        division_id = interaction.data['values'][0]
        self._selected_division = next(
            (division for division in self._divisions if division.get('id') == division_id),
            None,
        )

        if not self._selected_division:
            await interaction.followup.send("❌ Division not found.", ephemeral=True)
            return

        try:
            seasons_response = (
                self.supabase
                .table('seasons')
                .select('*')
                .eq('division_id', division_id)
                .order('created_at', desc=True)
                .execute()
            )
            seasons = seasons_response.data or []
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load seasons for division %s: %s", division_id, exc)
            seasons = []

        if not seasons:
            division_label = self.manager.format_division_display(self._selected_division)
            await interaction.followup.send(
                f"❌ No seasons found for {division_label}.",
                ephemeral=True,
            )
            return

        season_select = discord.ui.Select(
            placeholder="Select season...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=season.get('name', 'Unknown')[:100],
                    value=season.get('id', ''),
                    description="✅ Active" if season.get('active') else "⏸️ Inactive",
                )
                for season in seasons[:25]
            ],
        )
        season_select.callback = self.select_season
        season_view = discord.ui.View()
        season_view.add_item(season_select)

        division_label = self.manager.format_division_display(self._selected_division)
        active_flag = any(season.get('active') for season in seasons)
        status = "✅ Active season available" if active_flag else "⏸️ No active season"
        await interaction.followup.send(
            f"{division_label} selected. {status}. Choose a season:",
            view=season_view,
            ephemeral=True,
        )

    async def select_season(self, interaction: discord.Interaction) -> None:
        """Handle season selection."""
        await interaction.response.defer(ephemeral=True)
        season_id = interaction.data['values'][0]

        if not self._selected_division:
            await interaction.followup.send("❌ Please select a division before choosing a season.", ephemeral=True)
            return

        try:
            season = self.supabase.table('seasons').select('*').eq('id', season_id).limit(1).execute()
            if season.data:
                self._selected_season = season.data[0]
                # ✅ FIXED: Use manager to get teams scoped to guild
                division_id = self._selected_season.get('division_id')
                teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
                
                # Filter teams to this division for extra safety
                teams = [t for t in teams if t.get('division_id') == division_id]
                
                if not teams:
                    await interaction.followup.send("❌ No teams found.", ephemeral=True)
                    return

                team_select = discord.ui.Select(
                    placeholder="Select team...",
                    options=[
                        discord.SelectOption(
                            label=t.get('name', 'Unknown')[:100],
                            value=t.get('id', ''),
                            description=t.get('code', '?'),
                        )
                        for t in sorted(teams, key=lambda x: x.get('code', ''))
                    ],
                    row=0,
                )
                team_select.callback = self.select_team
                view = discord.ui.View()
                view.add_item(team_select)

                await interaction.followup.send(
                    f"Season: **{self._selected_season.get('name')}**\n\nNow select team:",
                    view=view,
                    ephemeral=True,
                )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch season: %s", exc)
            await interaction.followup.send("❌ Failed to fetch season.", ephemeral=True)

    async def select_team(self, interaction: discord.Interaction) -> None:
        """Handle team selection."""
        await interaction.response.defer(ephemeral=True)
        team_id = interaction.data['values'][0]

        try:
            team = self.supabase.table('teams').select('*').eq('id', team_id).limit(1).execute()
            if team.data:
                self._selected_team = team.data[0]
                # Now show player selection
                players = self.supabase.table('players').select('*').order('display_name', desc=False).limit(50).execute()
                if not players.data:
                    await interaction.followup.send("❌ No players found.", ephemeral=True)
                    return

                player_select = discord.ui.Select(
                    placeholder="Select player...",
                    options=[
                        discord.SelectOption(
                            label=p.get('display_name', 'Unknown')[:100],
                            value=p.get('id', ''),
                        )
                        for p in players.data
                    ],
                    row=0,
                )
                player_select.callback = self.select_player
                view = discord.ui.View()
                view.add_item(player_select)

                await interaction.followup.send(
                    f"Season: **{self._selected_season.get('name')}**\n"
                    f"Team: **{self._selected_team.get('name')}** (`{self._selected_team.get('code')}`)\n\n"
                    f"Now select player:",
                    view=view,
                    ephemeral=True,
                )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch team: %s", exc)
            await interaction.followup.send("❌ Failed to fetch team.", ephemeral=True)

    async def select_player(self, interaction: discord.Interaction) -> None:
        """Handle player selection and show confirmation."""
        await interaction.response.defer(ephemeral=True)
        player_id = interaction.data['values'][0]

        try:
            player = self.supabase.table('players').select('*').eq('id', player_id).limit(1).execute()
            if player.data:
                self._selected_player = player.data[0]

                # Check if already on roster
                existing_roster = self.repo.get_active_roster_entry(
                    self._selected_season['id'],
                    player_id
                )
                if existing_roster:
                    await interaction.followup.send(
                        f"⚠️ Player **{self._selected_player.get('display_name')}** is already on a roster.",
                        ephemeral=True,
                    )
                    return

                # Confirmation
                class ConfirmSignView(discord.ui.View):
                    def __init__(self) -> None:
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Confirm Sign", style=discord.ButtonStyle.success, emoji="✅")
                    async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await confirm_interaction.response.defer(ephemeral=True)
                        try:
                            # Insert roster entry
                            roster_data = {
                                'season_id': self._selected_season['id'],
                                'team_id': self._selected_team['id'],
                                'player_id': player_id,
                                'joined_at': datetime.utcnow().isoformat(),
                            }
                            result = self.supabase.table('rosters').insert(roster_data).execute()

                            if result.data:
                                logger.info(
                                    "✅ Force sign: %s → %s (%s) by %s",
                                    self._selected_player.get('display_name', '?'),
                                    self._selected_team.get('code', '?'),
                                    self._selected_season.get('name', '?'),
                                    confirm_interaction.user.id,
                                )
                                # Insert transaction log
                                self.repo.insert_transaction({
                                    'season_id': self._selected_season['id'],
                                    'transaction_type': 'admin_force_sign',
                                    'player_id': player_id,
                                    'team_id': self._selected_team['id'],
                                    'metadata': {
                                        'admin_user_id': str(confirm_interaction.user.id),
                                        'reason': 'admin_force_sign',
                                    },
                                })
                                await TeamPlayerCache.refresh(self.supabase)
                                await confirm_interaction.followup.send(
                                    f"✅ **{self._selected_player.get('display_name')}** force signed to "
                                    f"**{self._selected_team.get('name')}** for **{self._selected_season.get('name')}**",
                                    ephemeral=True,
                                )
                                await self.parent_view.update_display()
                            else:
                                raise Exception("No data returned from insert")
                        except Exception as exc:  # pragma: no cover
                            logger.error("Failed to force sign player: %s", exc)
                            await confirm_interaction.followup.send(
                                "❌ Failed to force sign player. Check server logs.",
                                ephemeral=True,
                            )

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await cancel_interaction.response.defer(ephemeral=True)
                        await cancel_interaction.followup.send("❌ Cancelled.", ephemeral=True)

                confirm_view = ConfirmSignView()
                confirm_view.supabase = self.supabase
                confirm_view.parent_view = self.parent_view
                confirm_view.repo = self.repo
                confirm_view._selected_season = self._selected_season
                confirm_view._selected_team = self._selected_team
                confirm_view._selected_player = self._selected_player

                embed = discord.Embed(
                    title="✅ Confirm Force Sign",
                    description=(
                        f"**Player**: {self._selected_player.get('display_name')}\n"
                        f"**Team**: {self._selected_team.get('name')} (`{self._selected_team.get('code')}`)\n"
                        f"**Season**: {self._selected_season.get('name')}\n\n"
                        f"Confirm force sign?"
                    ),
                    color=discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch player: %s", exc)
            await interaction.followup.send("❌ Failed to fetch player.", ephemeral=True)



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
            active_rosters = self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, joined_at, '
                'players(id, display_name), '
                'teams(code, name)'
            ).is_('left_at', 'null').order('joined_at', desc=True).limit(100).execute()
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
            roster = self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name), teams(name, code)'
            ).eq('id', roster_id).limit(1).execute()

            if roster.data:
                roster_entry = roster.data[0]
                player_name = roster_entry['players'].get('display_name', 'Unknown')
                team_code = roster_entry['teams'].get('code', '?')

                # Confirmation
                class ConfirmReleaseView(discord.ui.View):
                    def __init__(self) -> None:
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Confirm Release", style=discord.ButtonStyle.danger, emoji="🚪")
                    async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await confirm_interaction.response.defer(ephemeral=True)
                        try:
                            # Mark player as released
                            self.repo.mark_player_released(
                                roster_entry['season_id'],
                                roster_entry['player_id'],
                                roster_entry['team_id']
                            )
                            logger.info(
                                "✅ Force release: %s from %s by %s",
                                player_name,
                                team_code,
                                confirm_interaction.user.id,
                            )
                            # Insert transaction log
                            self.repo.insert_transaction({
                                'season_id': roster_entry['season_id'],
                                'transaction_type': 'admin_force_release',
                                'player_id': roster_entry['player_id'],
                                'team_id': roster_entry['team_id'],
                                'metadata': {
                                    'admin_user_id': str(confirm_interaction.user.id),
                                    'reason': 'admin_force_release',
                                },
                            })
                            await TeamPlayerCache.refresh(self.supabase)
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

                embed = discord.Embed(
                    title="⚠️ Confirm Force Release",
                    description=f"Release **{player_name}** from **{team_code}**?",
                    color=discord.Color.red(),
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
            active_rosters = self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name), teams(code, name)'
            ).is_('left_at', 'null').order('joined_at', desc=True).limit(100).execute()
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
            roster = self.supabase.table('rosters').select(
                'id, player_id, team_id, season_id, players(display_name), teams(name, code)'
            ).eq('id', roster_id).limit(1).execute()

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
            dest_team = self.supabase.table('teams').select('*').eq('id', destination_team_id).limit(1).execute()
            if dest_team.data:
                dest_team_data = dest_team.data[0]
                player_name = self._selected_roster['players'].get('display_name', 'Unknown')
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
                            # Transfer player
                            self.repo.transfer_player_between_teams(
                                self._selected_roster['season_id'],
                                self._selected_roster['player_id'],
                                self._selected_roster['team_id'],
                                destination_team_id,
                            )
                            logger.info(
                                "✅ Force transfer: %s from %s to %s by %s",
                                player_name,
                                current_code,
                                dest_code,
                                confirm_interaction.user.id,
                            )
                            # Insert transaction log
                            self.repo.insert_transaction({
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
                            await TeamPlayerCache.refresh(self.supabase)
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

                embed = discord.Embed(
                    title="✅ Confirm Force Transfer",
                    description=(
                        f"**Player**: {player_name}\n"
                        f"**From**: {current_code}\n"
                        f"**To**: {dest_code}\n\n"
                        f"Confirm transfer?"
                    ),
                    color=discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch destination team: %s", exc)
            await interaction.followup.send("❌ Failed to fetch team.", ephemeral=True)



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
            active_rosters = self.supabase.table('rosters').select(
                'id, player_id, season_id, joined_at, players(display_name, discord_id), seasons(name)'
            ).eq('team_id', team_id).is_('left_at', 'null').order('joined_at', desc=False).execute()

            team = self.supabase.table('teams').select('*').eq('id', team_id).limit(1).execute()
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

