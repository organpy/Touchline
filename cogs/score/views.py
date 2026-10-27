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
from utils.team_picker import TeamSelect, entries_from_cached_teams

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
# PLAYER STAT ENTRY MODAL (First 5 fields)
# ============================================================================

class PlayerStatEntryModal(discord.ui.Modal):
    """Modal for entering first 5 player stats"""

    def __init__(self, player_name: str, on_stats_entered: Callable):
        super().__init__(title=f"Stats for {player_name} (1/2)")
        self.player_name = player_name
        self.on_stats_entered = on_stats_entered

        self.goals_input = discord.ui.TextInput(label="Goals", placeholder="0", default="0")
        self.own_goals_input = discord.ui.TextInput(label="Own Goals", placeholder="0", default="0")
        self.points_input = discord.ui.TextInput(label="Points", placeholder="0", default="0")
        self.assists_input = discord.ui.TextInput(label="Assists", placeholder="0", default="0")
        self.saves_input = discord.ui.TextInput(label="Saves", placeholder="0", default="0")

        for item in (self.goals_input, self.own_goals_input, self.points_input, 
                     self.assists_input, self.saves_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            first_batch = {
                'goals': int(self.goals_input.value.strip()),
                'own_goals': int(self.own_goals_input.value.strip()),
                'points': int(self.points_input.value.strip()),
                'assists': int(self.assists_input.value.strip()),
                'saves': int(self.saves_input.value.strip())
            }

            async def on_second_submit(second_stats: dict, second_interaction: discord.Interaction):
                stat_entry = PlayerStatEntry(
                    player_id="",
                    player_name=self.player_name,
                    goals=first_batch['goals'],
                    own_goals=first_batch['own_goals'],
                    points=first_batch['points'],
                    assists=first_batch['assists'],
                    saves=first_batch['saves'],
                    blocks=second_stats['blocks'],
                    powerup_plays=second_stats['powerup_plays'],
                    powerups_used=second_stats['powerups_used']
                )
                await self.on_stats_entered(stat_entry, second_interaction)

            second_modal = PlayerStatEntryModalSecond(self.player_name, on_second_submit)
            
            # Defer the response, then send the second modal via a background task
            await interaction.response.defer()
            
            # Use a small delay to ensure the defer processes before sending the next modal
            import asyncio
            await asyncio.sleep(0.1)
            
            # Send the second modal via edit_original_response workaround
            try:
                await interaction.followup.send("📋 Now entering remaining stats...", ephemeral=True)
            except discord.HTTPException:
                pass
            
            # Create a minimal view with a button to trigger the second modal
            async def show_second_modal_button(button_interaction: discord.Interaction):
                await button_interaction.response.send_modal(second_modal)
            
            view = discord.ui.View()
            button = discord.ui.Button(label="📊 Continue to Part 2", style=discord.ButtonStyle.primary)
            button.callback = show_second_modal_button
            view.add_item(button)
            
            await interaction.followup.send("Click the button to continue:", view=view, ephemeral=True)

        except ValueError as e:
            await interaction.response.send_message(f"❌ Invalid input: {e}", ephemeral=True)


class PlayerStatEntryModalSecond(discord.ui.Modal):
    """Modal for remaining stats (Blocks, Powerup Plays, Powerups Used)"""

    def __init__(self, player_name: str, on_stats_entered: Callable):
        super().__init__(title=f"Stats for {player_name} (2/2)")
        self.player_name = player_name
        self.on_stats_entered = on_stats_entered

        self.blocks_input = discord.ui.TextInput(label="Blocks", placeholder="0", default="0")
        self.powerup_plays_input = discord.ui.TextInput(label="Powerup Plays", placeholder="0", default="0")
        self.powerups_used_input = discord.ui.TextInput(label="Powerups Used", placeholder="0", default="0")

        for item in (self.blocks_input, self.powerup_plays_input, self.powerups_used_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            second_stats = {
                'blocks': int(self.blocks_input.value.strip()),
                'powerup_plays': int(self.powerup_plays_input.value.strip()),
                'powerups_used': int(self.powerups_used_input.value.strip())
            }
            await self.on_stats_entered(second_stats, interaction)
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

        # Show stat entry modal
        async def on_stats_submitted(stat_entry: PlayerStatEntry, modal_interaction: discord.Interaction):
            stat_entry.player_id = cached_player.player_id
            self.team_match_entry.add_player_stats(stat_entry)
            await self.update_summary(modal_interaction)

        modal = PlayerStatEntryModal(cached_player.display_name, on_stats_submitted)
        await interaction.response.send_modal(modal)

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
        entries = entries_from_cached_teams(roster_cache.get_all_teams())
        self.add_item(TeamSelect(
            entries, on_pick=self.on_team1_select, placeholder="Select Team 1", custom_id="team1_select", row=0,
        ))
        self.add_item(TeamSelect(
            entries, on_pick=self.on_team2_select, placeholder="Select Team 2", custom_id="team2_select", row=1,
        ))

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
        self._busy = False

    async def _run(self, interaction: discord.Interaction, callback: Callable) -> None:
        """Run a button action once at a time. When it reports it is finished (True), the buttons go inactive;
        when it fails (False) they stay so the report can be retried: the database stores one game per report."""
        await interaction.response.defer()
        if self._busy:
            return
        self._busy = True
        try:
            finished = await callback(interaction)
        finally:
            self._busy = False
        if finished:
            for item in self.children:
                item.disabled = True
            try:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass  # the message is gone or the token expired; the buttons are dead either way
            self.stop()

    @discord.ui.button(label="✅ Confirm & Save", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, self.on_confirm)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, self.on_cancel)
