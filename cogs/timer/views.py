"""
Timer Views - Beautiful Discord UI components for match timer
"""

import discord
from discord.ext import tasks
from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)


class TimerControlView(discord.ui.View):
    """Main timer control panel with pause, resume, stop buttons"""

    def __init__(self, on_pause: Callable, on_resume: Callable, on_stop: Callable):
        super().__init__(timeout=None)
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_stop = on_stop
        self.is_running = True

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.secondary, emoji="⏸️", custom_id="timer_pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_running:
            self.is_running = False
            button.label = "▶ Resume"
            button.emoji = "▶️"
            await self.on_pause(interaction)
            # Don't update here - let the callback handle it

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.secondary, emoji="▶️", custom_id="timer_resume")
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_running:
            self.is_running = True
            button.label = "⏸ Pause"
            button.emoji = "⏸️"
            await self.on_resume(interaction)

    @discord.ui.button(label="🛑 End Match", style=discord.ButtonStyle.danger, emoji="🛑", custom_id="timer_stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_stop(interaction)
        self.stop()


class TimerConfirmView(discord.ui.View):
    """Confirmation view for ending timer"""

    def __init__(self, on_confirm: Callable, on_cancel: Callable):
        super().__init__(timeout=30)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.confirmed = False

    @discord.ui.button(label="✅ End Match", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        await self.on_confirm(interaction)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_cancel(interaction)
        self.stop()

    async def on_timeout(self):
        logger.warning("Timer confirmation view timed out")


class TeamSelectionForTimerView(discord.ui.View):
    """Select which teams are playing for timer"""

    def __init__(self, teams: list, on_select: Callable):
        super().__init__(timeout=60)
        self.teams = teams
        self.on_select = on_select
        self.selected_teams = []

        # Create buttons for each team
        for team in teams[:5]:  # Discord limit is 5 buttons per row, max 25 total
            button = discord.ui.Button(
                label=team['name'],
                emoji=team.get('emoji', '🏀'),
                style=discord.ButtonStyle.primary,
                custom_id=f"timer_team_{team['code']}"
            )
            button.callback = self._team_button_callback
            self.add_item(button)

    async def _team_button_callback(self, interaction: discord.Interaction):
        team_code = interaction.data['custom_id'].replace('timer_team_', '')
        team = next((t for t in self.teams if t['code'] == team_code), None)
        
        if team:
            if len(self.selected_teams) < 2:
                self.selected_teams.append(team)
            
            if len(self.selected_teams) == 2:
                await self.on_select(interaction, self.selected_teams)
                self.stop()


class TimerDisplayEmbed(discord.Embed):
    """Beautiful timer display embed"""

    def __init__(self, team1: dict, team2: dict, time_remaining: int, is_running: bool = True):
        super().__init__(
            title="⏱️ MATCH TIMER",
            color=discord.Color.blue() if is_running else discord.Color.red()
        )
        
        # Format time
        minutes = time_remaining // 60
        seconds = time_remaining % 60
        time_str = f"{minutes:02d}:{seconds:02d}"
        
        # Status indicator
        status = "🟢 RUNNING" if is_running else "🔴 PAUSED"
        
        # Add fields
        self.add_field(
            name=team1['name'],
            value=f"{team1.get('emoji', '🏀')} **{team1.get('score', 0)}**",
            inline=True
        )
        
        self.add_field(
            name="TIME",
            value=f"```\n{time_str}\n```",
            inline=True
        )
        
        self.add_field(
            name=team2['name'],
            value=f"**{team2.get('score', 0)}** {team2.get('emoji', '🏀')}",
            inline=True
        )
        
        self.add_field(
            name="Status",
            value=status,
            inline=False
        )
        
        self.set_footer(text="Press buttons below to control timer")

    @staticmethod
    def create_setup_embed(team1: dict, team2: dict, duration: int):
        """Create setup confirmation embed"""
        embed = discord.Embed(
            title="🏆 Timer Setup",
            description="Ready to start the match timer",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name=f"{team1.get('emoji', '🏀')} {team1['name']}",
            value=f"vs",
            inline=False
        )
        
        embed.add_field(
            name=f"{team2['name']} {team2.get('emoji', '🏀')}",
            value=f"Duration: **{duration // 60}:{duration % 60:02d}**",
            inline=False
        )
        
        return embed

    @staticmethod
    def create_end_embed(team1: dict, team2: dict, final_time: int):
        """Create match end embed"""
        embed = discord.Embed(
            title="🏁 MATCH ENDED",
            description="Match timer has ended",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name=f"{team1.get('emoji', '🏀')} {team1['name']}",
            value=f"**{team1.get('score', 0)}**",
            inline=True
        )
        
        embed.add_field(
            name="Final Time",
            value=f"```\n{final_time:02d}:{(final_time % 60):02d}\n```",
            inline=True
        )
        
        embed.add_field(
            name=f"{team2['name']} {team2.get('emoji', '🏀')}",
            value=f"**{team2.get('score', 0)}**",
            inline=True
        )
        
        return embed


class LiveScoreUpdateView(discord.ui.View):
    """Allow updating live scores during timer"""

    def __init__(self, on_team1_score: Callable, on_team2_score: Callable):
        super().__init__(timeout=None)
        self.on_team1_score = on_team1_score
        self.on_team2_score = on_team2_score

    @discord.ui.button(label="+1", style=discord.ButtonStyle.success, emoji="➕", custom_id="score_team1_plus")
    async def team1_plus_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_team1_score(interaction, 1)

    @discord.ui.button(label="-1", style=discord.ButtonStyle.danger, emoji="➖", custom_id="score_team1_minus")
    async def team1_minus_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_team1_score(interaction, -1)

    @discord.ui.button(label="Team 1 / Team 2", style=discord.ButtonStyle.secondary, emoji="⚖️", custom_id="score_separator")
    async def separator_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="+1", style=discord.ButtonStyle.success, emoji="➕", custom_id="score_team2_plus")
    async def team2_plus_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_team2_score(interaction, 1)

    @discord.ui.button(label="-1", style=discord.ButtonStyle.danger, emoji="➖", custom_id="score_team2_minus")
    async def team2_minus_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.on_team2_score(interaction, -1)
