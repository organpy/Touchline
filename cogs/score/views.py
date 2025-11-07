"""
Discord UI components for stat registration
Modal forms and persistent views
Imports models but NOT the main cog (to avoid circular imports)
"""

import discord
from discord.ext import commands
import logging
from typing import Callable, Optional

from .models import PlayerStatEntry, TeamMatchEntry, MatchEntry

logger = logging.getLogger(__name__)


# ============================================================================
# SCORE ENTRY MODAL
# ============================================================================

class ScoreEntryModal(discord.ui.Modal):
    """Modal for entering team scores"""

    def __init__(self, team1_name: str, team2_name: str, on_scores_entered: Callable):
        super().__init__(title="Enter Match Scores")
        self.on_scores_entered = on_scores_entered

        self.team1_score_input = discord.ui.TextInput(
            label=f"{team1_name} Score",
            placeholder="e.g., 4",
            min_length=1,
            max_length=3
        )

        self.team2_score_input = discord.ui.TextInput(
            label=f"{team2_name} Score",
            placeholder="e.g., 6",
            min_length=1,
            max_length=3
        )

        self.add_item(self.team1_score_input)
        self.add_item(self.team2_score_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            team1_score = int(self.team1_score_input.value.strip())
            team2_score = int(self.team2_score_input.value.strip())

            # Validation: no draws, both > 0
            if team1_score == team2_score:
                await interaction.response.send_message("❌ Scores cannot be equal (no draws allowed)", ephemeral=True)
                return

            if team1_score <= 0 or team2_score <= 0:
                await interaction.response.send_message("❌ Both scores must be > 0", ephemeral=True)
                return

            await self.on_scores_entered(team1_score, team2_score, interaction)
        except ValueError:
            await interaction.response.send_message("❌ Invalid score format (must be numbers)", ephemeral=True)


# ============================================================================
# PLAYER STAT ENTRY MODALS (Split into two: Offensive & Defensive)
# ============================================================================

class OffensiveStatsModal(discord.ui.Modal):
    """Modal for offensive stats (Goals, Points, Assists, etc.)"""

    def __init__(self, player_name: str, on_offensive_submitted: Callable):
        super().__init__(title=f"{player_name} - Offensive Stats")
        self.player_name = player_name
        self.on_offensive_submitted = on_offensive_submitted

        self.goals_input = discord.ui.TextInput(
            label="Goals",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.own_goals_input = discord.ui.TextInput(
            label="Own Goals",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.points_input = discord.ui.TextInput(
            label="Points",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.assists_input = discord.ui.TextInput(
            label="Assists",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.powerup_plays_input = discord.ui.TextInput(
            label="Powerup Plays",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        for item in (self.goals_input, self.own_goals_input, self.points_input, 
                     self.assists_input, self.powerup_plays_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            offensive_stats = {
                'goals': int(self.goals_input.value.strip()),
                'own_goals': int(self.own_goals_input.value.strip()),
                'points': int(self.points_input.value.strip()),
                'assists': int(self.assists_input.value.strip()),
                'powerup_plays': int(self.powerup_plays_input.value.strip())
            }
            # Defer so we can show the next modal via followup
            await interaction.response.defer()
            await self.on_offensive_submitted(offensive_stats, interaction)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid input: {e}", ephemeral=True)


class DefensiveStatsModal(discord.ui.Modal):
    """Modal for defensive stats (Saves, Blocks, Powerups Used)"""

    def __init__(self, player_name: str, offensive_stats: dict, on_stats_entered: Callable):
        super().__init__(title=f"{player_name} - Defensive Stats")
        self.player_name = player_name
        self.offensive_stats = offensive_stats
        self.on_stats_entered = on_stats_entered

        self.saves_input = discord.ui.TextInput(
            label="Saves",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.blocks_input = discord.ui.TextInput(
            label="Blocks",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        self.powerups_used_input = discord.ui.TextInput(
            label="Powerups Used",
            placeholder="0",
            default="0",
            min_length=1,
            max_length=2
        )

        for item in (self.saves_input, self.blocks_input, self.powerups_used_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stat_entry = PlayerStatEntry(
                player_id="",  # Will be set by caller
                player_name=self.player_name,
                goals=self.offensive_stats['goals'],
                own_goals=self.offensive_stats['own_goals'],
                points=self.offensive_stats['points'],
                assists=self.offensive_stats['assists'],
                saves=int(self.saves_input.value.strip()),
                blocks=int(self.blocks_input.value.strip()),
                powerup_plays=self.offensive_stats['powerup_plays'],
                powerups_used=int(self.powerups_used_input.value.strip())
            )
            await self.on_stats_entered(stat_entry, interaction)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid input: {e}", ephemeral=True)


# ============================================================================
# TEAM STATS ENTRY VIEW (Persistent)
# ============================================================================

class TeamStatsEntryView(discord.ui.View):
    """Persistent view for adding players and their stats to a team"""

    def __init__(
        self,
        team_match_entry: TeamMatchEntry,
        roster_cache,
        on_team_complete: Callable
    ):
        super().__init__(timeout=600)
        self.team_match_entry = team_match_entry
        self.roster_cache = roster_cache
        self.on_team_complete = on_team_complete

    async def on_player_selected(self, player_id: str, interaction: discord.Interaction):
        """Called when player is selected from dropdown"""
        cached_player = self.roster_cache.get_player_by_id(player_id)

        if not cached_player:
            await interaction.response.send_message("❌ Player not found", ephemeral=True)
            return

        # Show offensive stats modal first
        async def on_offensive_submitted(offensive_stats: dict, offensive_interaction: discord.Interaction):
            # After offensive stats, show defensive stats modal
            async def on_stats_submitted(stat_entry: PlayerStatEntry, defensive_interaction: discord.Interaction):
                stat_entry.player_id = cached_player.player_id
                self.team_match_entry.add_player_stats(stat_entry)
                await self.update_summary(defensive_interaction)

            defensive_modal = DefensiveStatsModal(cached_player.display_name, offensive_stats, on_stats_submitted)
            # Use followup.send to show modal after the offensive modal is deferred
            await offensive_interaction.followup.send(modal=defensive_modal)

        offensive_modal = OffensiveStatsModal(cached_player.display_name, on_offensive_submitted)
        await interaction.response.send_modal(offensive_modal)

    async def update_summary(self, interaction: discord.Interaction):
        """Update the embed showing current players added"""
        embed = discord.Embed(
            title=f"📊 {self.team_match_entry.team_name} Stats Entry",
            description=f"Score: **{self.team_match_entry.score}** | Players added: **{len(self.team_match_entry.players)}**",
            color=discord.Color.blue()
        )

        if self.team_match_entry.players:
            player_list = "\n".join(
                f"✅ {stat.player_name}: {stat.goals}G, {stat.own_goals}OG, {stat.points}P, {stat.assists}A, {stat.saves}S, {stat.blocks}B"
                for stat in self.team_match_entry.players.values()
            )
            embed.add_field(name="Players", value=player_list, inline=False)
        else:
            embed.add_field(name="Players", value="*No players added yet*", inline=False)

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="➕ Add Player", style=discord.ButtonStyle.primary)
    async def add_player_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        team_players = self.roster_cache.get_team_players(self.team_match_entry.team_code)

        if not team_players:
            await interaction.response.send_message(
                f"❌ No players available for {self.team_match_entry.team_name}",
                ephemeral=True
            )
            return

        # Filter out already added players
        available_players = [
            p for p in team_players
            if not self.team_match_entry.has_player(p.player_id)
        ]

        if not available_players:
            await interaction.response.send_message("✅ All available players have been added!", ephemeral=True)
            return

        # Create select for available players
        player_options = [
            discord.SelectOption(label=p.display_name, value=p.player_id)
            for p in available_players[:25]  # Discord limit
        ]

        select = discord.ui.Select(
            placeholder="Select a player",
            options=player_options,
            min_values=1,
            max_values=1
        )

        async def on_select(select_interaction: discord.Interaction):
            player_id = select_interaction.data['values'][0]
            await self.on_player_selected(player_id, select_interaction)

        select.callback = on_select
        view = discord.ui.View()
        view.add_item(select)

        await interaction.response.send_message("Select a player:", view=view, ephemeral=True)

    @discord.ui.button(label="✅ Done with This Team", style=discord.ButtonStyle.success)
    async def done_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.team_match_entry.players) == 0:
            await interaction.response.send_message(
                "❌ Please add at least one player before continuing",
                ephemeral=True
            )
            return

        await self.on_team_complete(interaction)


# ============================================================================
# TEAM SELECTION VIEW
# ============================================================================

class TeamSelectionView(discord.ui.View):
    """View for selecting teams"""

    def __init__(self, roster_cache, on_teams_selected: Callable):
        super().__init__(timeout=300)
        self.roster_cache = roster_cache
        self.on_teams_selected = on_teams_selected
        self.team1_code = None
        self.team2_code = None

        # Create team selects
        teams = roster_cache.get_all_teams()
        team_options = [
            discord.SelectOption(label=t.name, value=t.code, emoji=t.emoji)
            for t in teams
        ]

        team1_select = discord.ui.Select(
            custom_id="team1_select",
            placeholder="Select Team 1",
            options=team_options[:25],
            min_values=1,
            max_values=1
        )
        team1_select.callback = self.on_team1_select
        self.add_item(team1_select)

        team2_select = discord.ui.Select(
            custom_id="team2_select",
            placeholder="Select Team 2",
            options=team_options[:25],
            min_values=1,
            max_values=1
        )
        team2_select.callback = self.on_team2_select
        self.add_item(team2_select)

    async def on_team1_select(self, interaction: discord.Interaction):
        self.team1_code = interaction.data['values'][0]
        await interaction.response.defer()

    async def on_team2_select(self, interaction: discord.Interaction):
        self.team2_code = interaction.data['values'][0]

        if self.team1_code and self.team2_code:
            if self.team1_code == self.team2_code:
                await interaction.response.send_message("❌ Teams must be different!", ephemeral=True)
                self.team2_code = None
                return

            await self.on_teams_selected(self.team1_code, self.team2_code, interaction)
        else:
            await interaction.response.defer()


# ============================================================================
# MATCH CONFIRMATION VIEW
# ============================================================================

class MatchConfirmationView(discord.ui.View):
    """Final confirmation before saving to database"""

    def __init__(self, match_entry: MatchEntry, on_confirm: Callable, on_cancel: Callable):
        super().__init__(timeout=300)
        self.match_entry = match_entry
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    @discord.ui.button(label="✅ Confirm & Save", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.on_confirm(interaction)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.on_cancel(interaction)
