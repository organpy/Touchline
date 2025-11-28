"""Draft Picks Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, List

import discord
from discord import app_commands

from db import queries
from db import get_supabase
from db.transactions_repository import TransactionsRepository
from cogs.admin.common import LazyInitView
from cogs.admin.base import AdminBaseManager
from cogs.transactions.pick_trading import (
    get_nbl_division, validate_nbl_season, get_available_picks_for_team, 
    format_pick, NBL_LEAGUE_TYPE
)

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class PicksActionsView(discord.ui.View):
    """Buttons for draft picks management."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.original_message: Optional[discord.Message] = None
        self.repo = TransactionsRepository(supabase)

    def create_embed(self) -> discord.Embed:
        """Build dark minimalistic draft picks management embed."""
        embed = discord.Embed(
            title="Draft Picks",
            description="Seed, reassign, and view draft picks.",
            color=0x2B2D31,  # Dark theme
        )
        embed.add_field(
            name="Actions",
            value=(
                "**Seed** - Generate picks for season\n"
                "**Reassign** - Transfer ownership\n"
                "**View** - List all picks\n"
                "**By Team** - Team pick ownership"
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
            logger.error("Failed to update draft picks management embed: %s", exc)

    @discord.ui.button(label="Seed", style=discord.ButtonStyle.secondary, row=0)
    async def seed_picks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = SeedPicksView(self.supabase, self, self.repo)
        await view.prepare()
        embed = discord.Embed(
            title="🌱 Seed Draft Picks",
            description="Generate draft picks for a season. Select season and number of teams.",
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Reassign", style=discord.ButtonStyle.secondary, row=0)
    async def reassign_pick(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ReassignPickView(self.supabase, self, self.repo)
        await view.prepare()
        embed = discord.Embed(
            title="🔄 Reassign Draft Pick",
            description="Select pick and new owner.",
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="View", style=discord.ButtonStyle.secondary, row=0)
    async def view_picks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ViewPicksView(self.supabase, self)
        await view.prepare()
        embed = discord.Embed(
            title="📋 View All Picks",
            description="Select season to view draft order.",
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="By Team", style=discord.ButtonStyle.secondary, row=0)
    async def view_by_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = ViewPicksByTeamView(self.supabase, self)
        await view.prepare()
        embed = discord.Embed(
            title="🗂️ View Team Picks",
            description="Select team to view their draft picks.",
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to main dashboard."""
        await interaction.response.defer()
        self.stop()


class SeedPicksView(LazyInitView):
    """Seed draft picks: Season → Num Teams → Confirm."""

    def __init__(self, supabase: Any, parent_view: PicksActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None
        self._selected_division: Optional[Dict[str, Any]] = None
        self._selected_season: Optional[Dict[str, Any]] = None
        self._divisions: List[Dict[str, Any]] = []
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load available divisions before season selection (NBL ONLY)."""
        try:
            self._divisions = await self.manager.get_guild_divisions(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load divisions for seed picks: %s", exc)
            self._divisions = []

        # ✅ SAFETY: Filter to NBL only
        self._divisions = [d for d in self._divisions if d.get('league_type') == NBL_LEAGUE_TYPE]

        if not self._divisions:
            empty_select = discord.ui.Select(
                placeholder="❌ Draft picks only available for NBL division.",
                options=[discord.SelectOption(label="NBL division not configured", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        division_select = discord.ui.Select(
            placeholder="Select NBL division...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(division.get('name') or 'NBL')[:100],
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
        """Handle division selection before seeding seasons."""
        await interaction.response.defer(ephemeral=True)

        division_id = interaction.data['values'][0]
        self._selected_season = None
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
            placeholder="Select season to seed picks...",
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
            f"{division_label} selected. {status}. Choose a season to seed:",
            view=season_view,
            ephemeral=True,
        )

    async def select_season(self, interaction: discord.Interaction) -> None:
        """Handle season selection."""
        if not self._selected_division:
            await interaction.response.send_message("❌ Please select a division first.", ephemeral=True)
            return

        season_id = interaction.data['values'][0]

        try:
            season = self.supabase.table('seasons').select('*').eq('id', season_id).limit(1).execute()
            if season.data:
                self._selected_season = season.data[0]

                # Check if picks already exist
                existing_picks = self.repo.list_draft_picks(season_id)
                if existing_picks:
                    await interaction.response.send_message(
                        f"⚠️ **{self._selected_season.get('name')}** already has {len(existing_picks)} draft picks.",
                        ephemeral=True,
                    )
                    return

                # Show num teams input
                modal = NumTeamsModal(self.supabase, self, self._selected_season, self.repo)
                await interaction.response.send_modal(modal)
            else:
                await interaction.response.send_message("❌ Season not found.", ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch season: %s", exc)
            if interaction.response.is_done():
                await interaction.followup.send("❌ Failed to fetch season.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Failed to fetch season.", ephemeral=True)



class NumTeamsModal(discord.ui.Modal, title="Number of Teams for Seeding"):
    """Modal to input number of teams."""

    num_teams = discord.ui.TextInput(
        label="Number of Teams",
        placeholder="e.g., 8",
        max_length=3,
    )
    num_rounds = discord.ui.TextInput(
        label="Number of Rounds",
        placeholder="e.g., 3 (optional)",
        required=False,
        max_length=3,
    )

    def __init__(self, supabase: Any, view: SeedPicksView, season: Dict[str, Any], repo: TransactionsRepository) -> None:
        super().__init__()
        self.supabase = supabase
        self.view = view
        self.season = season
        self.repo = repo

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            num_teams = int(self.num_teams.value.strip())
            num_rounds = int(self.num_rounds.value.strip()) if self.num_rounds.value.strip() else 1

            if num_teams < 2 or num_teams > 50:
                await interaction.followup.send("❌ Number of teams must be between 2 and 50.", ephemeral=True)
                return

            if num_rounds < 1 or num_rounds > 10:
                await interaction.followup.send("❌ Number of rounds must be between 1 and 10.", ephemeral=True)
                return

            # ✅ FIXED: Use manager to get teams scoped to guild
            division_id = self.season.get('division_id')
            teams = await self.view.manager.get_teams_for_guild(self.view.parent_view.guild_id)
            
            # Filter teams to this division for extra safety
            teams = [t for t in teams if t.get('division_id') == division_id]
            
            if not teams:
                await interaction.followup.send(
                    "❌ No teams found for the selected division.",
                    ephemeral=True,
                )
                return
            teams = sorted(teams, key=lambda team: team.get('code', '').upper())
            if len(teams) < num_teams:
                await interaction.followup.send(
                    f"❌ Only {len(teams)} teams exist, but {num_teams} requested.",
                    ephemeral=True,
                )
                return

            # Confirmation
            class ConfirmSeedView(discord.ui.View):
                def __init__(self) -> None:
                    super().__init__(timeout=60)

                @discord.ui.button(label="Confirm Seed", style=discord.ButtonStyle.success, emoji="✅")
                async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                    await confirm_interaction.response.defer(ephemeral=True)
                    try:
                        # Generate picks
                        picks_to_insert = []
                        overall_count = 1

                        for round_num in range(1, num_rounds + 1):
                            for team_idx in range(num_teams):
                                team = teams[team_idx]
                                picks_to_insert.append({
                                    'season_id': self.season['id'],
                                    'round': round_num,
                                    'overall': overall_count,
                                    'current_owner_team_id': team.get('id'),
                                    'original_team_id': team.get('id'),
                                })
                                overall_count += 1

                        # Batch insert
                        if picks_to_insert:
                            result = self.supabase.table('draft_picks').insert(picks_to_insert).execute()
                            if result.data:
                                logger.info(
                                    "✅ Seeded %d draft picks for %s (%d teams, %d rounds) by %s",
                                    len(picks_to_insert),
                                    self.season.get('name', '?'),
                                    num_teams,
                                    num_rounds,
                                    confirm_interaction.user.id,
                                )
                                await confirm_interaction.followup.send(
                                    f"✅ Seeded **{len(picks_to_insert)}** draft picks for "
                                    f"**{self.season.get('name')}** ({num_teams} teams × {num_rounds} rounds)",
                                    ephemeral=True,
                                )
                                await self.view.parent_view.update_display()
                            else:
                                raise Exception("No data returned from insert")
                        else:
                            await confirm_interaction.followup.send("❌ No picks generated.", ephemeral=True)
                    except Exception as exc:  # pragma: no cover
                        logger.error("Failed to seed picks: %s", exc)
                        await confirm_interaction.followup.send(
                            "❌ Failed to seed picks. Check server logs.",
                            ephemeral=True,
                        )

                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                    await cancel_interaction.response.defer(ephemeral=True)
                    await cancel_interaction.followup.send("❌ Cancelled.", ephemeral=True)

            confirm_view = ConfirmSeedView()
            confirm_view.supabase = self.supabase
            confirm_view.view = self.view
            confirm_view.season = self.season

            embed = discord.Embed(
                title="✅ Confirm Seed Draft Picks",
                description=(
                    f"**Season**: {self.season.get('name')}\n"
                    f"**Teams**: {num_teams}\n"
                    f"**Rounds**: {num_rounds}\n"
                    f"**Total Picks**: {num_teams * num_rounds}\n\n"
                    f"Generate draft picks?"
                ),
                color=discord.Color.purple(),
            )
            await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ Please enter valid numbers.", ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to seed picks: %s", exc)
            await interaction.followup.send("❌ Failed to seed picks. Check server logs.", ephemeral=True)


class ReassignPickView(LazyInitView):
    """Reassign pick ownership."""

    def __init__(self, supabase: Any, parent_view: PicksActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None
        self._selected_pick: Optional[Dict[str, Any]] = None
        self._selected_division: Optional[Dict[str, Any]] = None
        self._selected_season: Optional[Dict[str, Any]] = None
        self._divisions: List[Dict[str, Any]] = []
        self._team_map: Dict[str, Dict[str, Any]] = {}
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load available divisions before narrowing to seasons (NBL ONLY)."""
        try:
            self._divisions = await self.manager.get_guild_divisions(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load divisions for reassign view: %s", exc)
            self._divisions = []

        # ✅ SAFETY: Filter to NBL only
        self._divisions = [d for d in self._divisions if d.get('league_type') == NBL_LEAGUE_TYPE]

        if not self._divisions:
            empty_select = discord.ui.Select(
                placeholder="❌ Draft picks only available for NBL division.",
                options=[discord.SelectOption(label="NBL division not configured", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        division_select = discord.ui.Select(
            placeholder="Select NBL division...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(division.get('name') or 'NBL')[:100],
                    value=division.get('id', ''),
                    description="NBL - Primary League",
                )
                for division in sorted(
                    self._divisions,
                    key=lambda d: d.get('name') or '',
                )
            ],
            row=0,
        )
        division_select.callback = self.select_division
        self.add_item(division_select)

    async def select_division(self, interaction: discord.Interaction) -> None:
        """Handle division selection prior to showing seasons."""
        await interaction.response.defer(ephemeral=True)

        division_id = interaction.data['values'][0]
        self._selected_season = None
        self._team_map = {}
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
        """Handle season selection and present draft picks."""
        if not self._selected_division:
            await interaction.response.send_message("❌ Please select a division first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        season_id = interaction.data['values'][0]

        try:
            season_response = self.supabase.table('seasons').select('*').eq('id', season_id).limit(1).execute()
            if not season_response.data:
                await interaction.followup.send("❌ Season not found.", ephemeral=True)
                return

            self._selected_season = season_response.data[0]
            
            # ✅ FIXED: Use manager to get teams scoped to guild
            division_id = self._selected_season.get('division_id')
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
            
            # Filter teams to this division for extra safety
            teams = [t for t in teams if t.get('division_id') == division_id]
            
            self._team_map = {team.get('id'): team for team in (teams or [])}

            picks = self.repo.list_draft_picks(season_id)
            if not picks:
                await interaction.followup.send("❌ No draft picks found for this season.", ephemeral=True)
                return

            def owner_label(pick: Dict[str, Any]) -> str:
                team = self._team_map.get(pick.get('current_owner_team_id'))
                if team:
                    code = (team.get('code') or '?').upper()
                    return f"Owner: {code}"
                return f"Owner ID: {str(pick.get('current_owner_team_id'))[:10]}"

            pick_select = discord.ui.Select(
                placeholder="Select pick to reassign...",
                options=[
                    discord.SelectOption(
                        label=f"R{p.get('round', '?')}.{p.get('position_in_round', '?')} (Overall {p.get('overall', '?')})",
                        value=p.get('id', ''),
                        description=owner_label(p),
                    )
                    for p in picks[:25]
                ],
                row=0,
            )
            pick_select.callback = self.select_pick
            picks_view = discord.ui.View()
            picks_view.add_item(pick_select)

            season_name = self._selected_season.get('name', 'Unknown')
            await interaction.followup.send(
                f"Season **{season_name}** loaded. Choose a pick to reassign:",
                view=picks_view,
                ephemeral=True,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch picks for season %s: %s", season_id, exc)
            await interaction.followup.send("❌ Failed to fetch picks.", ephemeral=True)

    async def select_pick(self, interaction: discord.Interaction) -> None:
        """Handle pick selection."""
        await interaction.response.defer(ephemeral=True)
        pick_id = interaction.data['values'][0]

        try:
            pick_result = self.supabase.table('draft_picks').select('*').eq('id', pick_id).limit(1).execute()
            if pick_result.data:
                self._selected_pick = pick_result.data[0]

                # ✅ FIXED: Use manager to get teams scoped to guild (CRITICAL BUG FIX!)
                teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
                
                # Filter teams to this division for extra safety
                division_id = self._selected_season.get('division_id') if self._selected_season else None
                if division_id:
                    teams = [t for t in teams if t.get('division_id') == division_id]
                
                if not teams:
                    await interaction.followup.send("❌ No teams found.", ephemeral=True)
                    return

                teams = sorted(teams, key=lambda x: x.get('code', '').upper())
                self._team_map = {team.get('id'): team for team in teams}

                team_select = discord.ui.Select(
                    placeholder="Select new owner team...",
                    options=[
                        discord.SelectOption(
                            label=t.get('name', 'Unknown')[:100],
                            value=t.get('id', ''),
                            description=t.get('code', '?'),
                        )
                        for t in teams[:25]
                    ],
                    row=0,
                )
                team_select.callback = self.select_new_owner
                view = discord.ui.View()
                view.add_item(team_select)

                owner = self._team_map.get(self._selected_pick.get('current_owner_team_id'))
                owner_label = f"{owner.get('name')} (`{owner.get('code')}`)" if owner else self._selected_pick.get('current_owner_team_id', '?')

                embed = discord.Embed(
                    title="Current Pick Details",
                    description=(
                        f"Round: {self._selected_pick.get('round')}.{self._selected_pick.get('position_in_round')}\n"
                        f"Overall: {self._selected_pick.get('overall')}\n"
                        f"Current Owner: {owner_label}\n\n"
                        f"Select new owner:"
                    ),
                    color=discord.Color.purple(),
                )
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch pick: %s", exc)
            await interaction.followup.send("❌ Failed to fetch pick.", ephemeral=True)

    async def select_new_owner(self, interaction: discord.Interaction) -> None:
        """Handle new owner selection."""
        await interaction.response.defer(ephemeral=True)
        new_team_id = interaction.data['values'][0]

        if not self._selected_pick:
            await interaction.followup.send("❌ Please select a draft pick first.", ephemeral=True)
            return

        try:
            new_team = self.supabase.table('teams').select('*').eq('id', new_team_id).limit(1).execute()
            if new_team.data:
                new_team_data = new_team.data[0]
                new_team_label = f"{new_team_data.get('name')} (`{new_team_data.get('code')}`)"
                current_owner = self._team_map.get(self._selected_pick.get('current_owner_team_id'))
                current_owner_label = (
                    f"{current_owner.get('name')} (`{current_owner.get('code')}`)"
                    if current_owner else str(self._selected_pick.get('current_owner_team_id'))[:10]
                )

                # Confirmation
                class ConfirmReassignView(discord.ui.View):
                    def __init__(self) -> None:
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Confirm Reassign", style=discord.ButtonStyle.success, emoji="✅")
                    async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await confirm_interaction.response.defer(ephemeral=True)
                        try:
                            result = self.repo.update_draft_pick_owner(self._selected_pick.get('id'), new_team_id)
                            if result:
                                logger.info(
                                    "✅ Draft pick reassigned: R%d.%d (overall %d) from %s to %s by %s",
                                    self._selected_pick.get('round', '?'),
                                    self._selected_pick.get('position_in_round', '?'),
                                    self._selected_pick.get('overall', '?'),
                                    self.team_map.get(self._selected_pick.get('current_owner_team_id', '?'), {}).get('code', '?'),
                                    new_team_data.get('code', '?'),
                                    confirm_interaction.user.id,
                                )
                                self._selected_pick['current_owner_team_id'] = new_team_id
                                await confirm_interaction.followup.send(
                                    f"✅ Pick reassigned to **{new_team_data.get('name')}** (`{new_team_data.get('code')}`)",
                                    ephemeral=True,
                                )
                                await self.parent_view.update_display()
                            else:
                                raise Exception("No data returned from update")
                        except Exception as exc:  # pragma: no cover
                            logger.error("Failed to reassign pick: %s", exc)
                            await confirm_interaction.followup.send(
                                "❌ Failed to reassign pick. Check server logs.",
                                ephemeral=True,
                            )

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                    async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                        await cancel_interaction.response.defer(ephemeral=True)
                        await cancel_interaction.followup.send("❌ Cancelled.", ephemeral=True)

                confirm_view = ConfirmReassignView()
                confirm_view.supabase = self.supabase
                confirm_view.parent_view = self.parent_view
                confirm_view.repo = self.repo
                confirm_view._selected_pick = self._selected_pick
                confirm_view.new_team_data = new_team_data
                confirm_view.team_map = self._team_map

                from utils.team_colors import get_team_color
                team_color = get_team_color(new_team_data.get('code'))
                embed = discord.Embed(
                    title="✅ Confirm Reassign",
                    description=(
                        f"**Pick**: R{self._selected_pick.get('round')}.{self._selected_pick.get('position_in_round')} "
                        f"(Overall: {self._selected_pick.get('overall')})\n"
                        f"**From**: {current_owner_label}\n"
                        f"**To**: {new_team_label}\n\n"
                        f"Confirm reassignment?"
                    ),
                    color=team_color,
                )
                await interaction.followup.send(embed=embed, view=confirm_view, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch new team: %s", exc)
            await interaction.followup.send("❌ Failed to fetch team.", ephemeral=True)



class ViewPicksView(LazyInitView):
    """View all picks for a season in order."""

    def __init__(self, supabase: Any, parent_view: PicksActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self.repo = TransactionsRepository(supabase)
        self._divisions: List[Dict[str, Any]] = []
        self._selected_division: Optional[Dict[str, Any]] = None
        self._selected_season: Optional[Dict[str, Any]] = None
        self._team_map: Dict[str, Dict[str, Any]] = {}
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load divisions first before narrowing to seasons (NBL ONLY)."""
        try:
            self._divisions = await self.manager.get_guild_divisions(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load divisions for view picks: %s", exc)
            self._divisions = []

        # ✅ SAFETY: Filter to NBL only
        self._divisions = [d for d in self._divisions if d.get('league_type') == NBL_LEAGUE_TYPE]

        if not self._divisions:
            empty_select = discord.ui.Select(
                placeholder="❌ Draft picks only available for NBL division.",
                options=[discord.SelectOption(label="NBL division not configured", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        division_select = discord.ui.Select(
            placeholder="Select NBL division...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(division.get('name') or 'NBL')[:100],
                    value=division.get('id', ''),
                    description="NBL - Primary League",
                )
                for division in sorted(
                    self._divisions,
                    key=lambda d: d.get('name') or '',
                )
            ],
            row=0,
        )
        division_select.callback = self.select_division
        self.add_item(division_select)

    async def select_division(self, interaction: discord.Interaction) -> None:
        """Handle division selection and present seasons."""
        await interaction.response.defer(ephemeral=True)

        division_id = interaction.data['values'][0]
        self._selected_division = next(
            (division for division in self._divisions if division.get('id') == division_id),
            None,
        )
        self._selected_season = None
        self._team_map = {}

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
        season_select.callback = self.view_season_picks
        season_view = discord.ui.View()
        season_view.add_item(season_select)

        division_label = self.manager.format_division_display(self._selected_division)
        active_flag = any(season.get('active') for season in seasons)
        status = "✅ Active season available" if active_flag else "⏸️ No active season"
        await interaction.followup.send(
            f"{division_label} selected. {status}. Choose a season to view picks:",
            view=season_view,
            ephemeral=True,
        )

    async def view_season_picks(self, interaction: discord.Interaction) -> None:
        """Display all picks for season."""
        if not self._selected_division:
            await interaction.response.send_message("❌ Please select a division first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        season_id = interaction.data['values'][0]

        try:
            season_response = self.supabase.table('seasons').select('*').eq('id', season_id).limit(1).execute()
            if not season_response.data:
                await interaction.followup.send("❌ Season not found.", ephemeral=True)
                return

            self._selected_season = season_response.data[0]
            
            # ✅ FIXED: Use manager to get teams scoped to guild
            division_id = self._selected_season.get('division_id')
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
            
            # Filter teams to this division for extra safety
            teams = [t for t in teams if t.get('division_id') == division_id]
            
            self._team_map = {team.get('id'): team for team in (teams or [])}

            picks = self.repo.list_draft_picks(season_id)
            if not picks:
                await interaction.followup.send("❌ No draft picks found.", ephemeral=True)
                return

            season_name = self._selected_season.get('name', 'Unknown')

            def owner_value(pick: Dict[str, Any]) -> str:
                team = self._team_map.get(pick.get('current_owner_team_id'))
                if team:
                    code = (team.get('code') or '?').upper()
                    return f"Owner: `{code}`"
                owner_id_short = str(pick.get('current_owner_team_id', '?'))[:10]
                return f"Owner ID: `{owner_id_short}`"

            embeds = []
            for i in range(0, len(picks), 20):
                chunk = picks[i:i + 20]
                embed = discord.Embed(
                    title=f"📋 {season_name} - Draft Order",
                    description=f"Page {len(embeds) + 1} ({i + 1}-{min(i + 20, len(picks))} of {len(picks)})",
                    color=discord.Color.purple(),
                )

                for pick in chunk:
                    embed.add_field(
                        name=f"#{pick.get('overall', '?')} - R{pick.get('round', '?')}.{pick.get('position_in_round', '?')}",
                        value=owner_value(pick),
                        inline=True,
                    )

                embeds.append(embed)

            if embeds:
                await interaction.followup.send(embed=embeds[0], ephemeral=True)
                for embed in embeds[1:]:
                    await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch picks for season %s: %s", season_id, exc)
            await interaction.followup.send("❌ Failed to fetch picks.", ephemeral=True)

class ViewPicksByTeamView(LazyInitView):
    """View picks owned by a specific team."""

    def __init__(self, supabase: Any, parent_view: PicksActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self._divisions: List[Dict[str, Any]] = []
        self._selected_division: Optional[Dict[str, Any]] = None
        self._selected_season: Optional[Dict[str, Any]] = None
        self._season_name: Optional[str] = None
        self._teams: List[Dict[str, Any]] = []
        self._team_map: Dict[str, Dict[str, Any]] = {}
        self.manager = AdminBaseManager()  # ✅ NEW

    async def populate(self) -> None:
        """Load divisions prior to season/team filtering (NBL ONLY)."""
        try:
            self._divisions = await self.manager.get_guild_divisions(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load divisions for view team picks: %s", exc)
            self._divisions = []

        # ✅ SAFETY: Filter to NBL only
        self._divisions = [d for d in self._divisions if d.get('league_type') == NBL_LEAGUE_TYPE]

        if not self._divisions:
            empty_select = discord.ui.Select(
                placeholder="❌ Draft picks only available for NBL division.",
                options=[discord.SelectOption(label="NBL division not configured", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        division_select = discord.ui.Select(
            placeholder="Select NBL division...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(division.get('name') or 'NBL')[:100],
                    value=division.get('id', ''),
                    description="NBL - Primary League",
                )
                for division in sorted(
                    self._divisions,
                    key=lambda d: d.get('name') or '',
                )
            ],
            row=0,
        )
        division_select.callback = self.select_division
        self.add_item(division_select)

    async def select_division(self, interaction: discord.Interaction) -> None:
        """Handle division selection and present seasons."""
        await interaction.response.defer(ephemeral=True)

        division_id = interaction.data['values'][0]
        self._selected_division = next(
            (division for division in self._divisions if division.get('id') == division_id),
            None,
        )
        self._selected_season = None
        self._season_name = None
        self._teams = []
        self._team_map = {}

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
            f"{division_label} selected. {status}. Choose a season to filter teams:",
            view=season_view,
            ephemeral=True,
        )

    async def select_season(self, interaction: discord.Interaction) -> None:
        """Handle season selection and present teams."""
        if not self._selected_division:
            await interaction.response.send_message("❌ Please select a division first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        season_id = interaction.data['values'][0]

        try:
            season_response = self.supabase.table('seasons').select('*').eq('id', season_id).limit(1).execute()
            if not season_response.data:
                await interaction.followup.send("❌ Season not found.", ephemeral=True)
                return

            self._selected_season = season_response.data[0]
            self._season_name = self._selected_season.get('name', 'Unknown')
            
            # ✅ FIXED: Use manager to get teams scoped to guild
            division_id = self._selected_season.get('division_id')
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
            
            # Filter teams to this division for extra safety
            teams = [t for t in teams if t.get('division_id') == division_id]
            
            if not teams:
                await interaction.followup.send("❌ No teams found for this season.", ephemeral=True)
                return

            self._teams = sorted(teams, key=lambda t: (t.get('code') or '').upper())
            self._team_map = {team.get('id'): team for team in self._teams}

            team_select = discord.ui.Select(
                placeholder="Select team...",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=team.get('name', 'Unknown')[:100],
                        value=team.get('id', ''),
                        description=team.get('code', '?'),
                    )
                    for team in self._teams[:25]
                ],
            )
            team_select.callback = self.view_team_picks
            team_view = discord.ui.View()
            team_view.add_item(team_select)

            await interaction.followup.send(
                f"Season **{self._season_name}** loaded. Choose a team to view picks:",
                view=team_view,
                ephemeral=True,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to load teams for season %s: %s", season_id, exc)
            await interaction.followup.send("❌ Failed to load teams.", ephemeral=True)

    async def view_team_picks(self, interaction: discord.Interaction) -> None:
        """Display picks owned by team."""
        if not self._selected_season:
            await interaction.response.send_message("❌ Please select a season first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        team_id = interaction.data['values'][0]

        try:
            picks_result = (
                self.supabase
                .table('draft_picks')
                .select('*')
                .eq('season_id', self._selected_season.get('id'))
                .eq('current_owner_team_id', team_id)
                .order('overall')
                .execute()
            )
            picks = picks_result.data if picks_result.data else []

            team_data = self._team_map.get(team_id)
            if not team_data:
                team_response = self.supabase.table('teams').select('*').eq('id', team_id).limit(1).execute()
                if not team_response.data:
                    await interaction.followup.send("❌ Team not found.", ephemeral=True)
                    return
                team_data = team_response.data[0]

            from utils.team_colors import get_team_color
            team_color = get_team_color(team_data.get('code'))
            embed = discord.Embed(
                title=f"🗂️ {team_data.get('name', 'Unknown')} Draft Picks",
                description=(
                    f"Code: `{team_data.get('code', '?')}`\n"
                    f"Season: **{self._season_name or self._selected_season.get('name', 'Unknown')}**"
                ),
                color=team_color,
            )

            if not picks:
                embed.add_field(name="Picks", value="No picks currently owned in this season.", inline=False)
            else:
                pick_lines = [
                    f"#{p.get('overall', '?')} - R{p.get('round', '?')}.{p.get('position_in_round', '?')}"
                    for p in picks
                ]
                embed.add_field(name=f"Picks ({len(picks)})", value="\n".join(pick_lines), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch team picks for team %s: %s", team_id, exc)
            await interaction.followup.send("❌ Failed to fetch picks.", ephemeral=True)

