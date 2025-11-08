"""
Timer Cog - Match timer for time-based competitions (10-minute games)
Supports pause/resume/stop with beautiful UI and live score updates
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from db import get_supabase, queries
from utils.config_manager import get_config_manager
from .views import (
    TimerControlView,
    TimerConfirmView,
    TeamSelectionForTimerView,
    TimerDisplayEmbed,
    LiveScoreUpdateView
)

logger = logging.getLogger(__name__)


class TimerSession:
    """Represents an active match timer session"""

    def __init__(
        self,
        message_id: int,
        channel_id: int,
        guild_id: int,
        team1: Dict[str, Any],
        team2: Dict[str, Any],
        duration_seconds: int
    ):
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.team1 = team1
        self.team2 = team2
        self.duration_seconds = duration_seconds
        self.elapsed_seconds = 0
        self.is_running = True
        self.created_at = datetime.utcnow()
        self.paused_at: Optional[datetime] = None

    @property
    def time_remaining(self) -> int:
        """Get seconds remaining"""
        return max(0, self.duration_seconds - self.elapsed_seconds)

    @property
    def is_finished(self) -> bool:
        """Check if timer has finished"""
        return self.time_remaining <= 0

    def pause(self):
        """Pause the timer"""
        self.is_running = False
        self.paused_at = datetime.utcnow()

    def resume(self):
        """Resume the timer"""
        self.is_running = True
        self.paused_at = None

    def tick(self):
        """Advance timer by 1 second"""
        if self.is_running and not self.is_finished:
            self.elapsed_seconds += 1


class TimerCog(commands.Cog):
    """Cog for match timer functionality"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.config_manager = get_config_manager()
        self.active_timers: Dict[int, TimerSession] = {}  # guild_id -> TimerSession
        self.timer_loop.start()

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.timer_loop.cancel()

    @tasks.loop(seconds=1)
    async def timer_loop(self):
        """Main timer loop - runs every second"""
        try:
            guild_ids_to_remove = []
            
            for guild_id, session in self.active_timers.items():
                # Advance timer
                session.tick()
                
                # Update display
                try:
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        guild_ids_to_remove.append(guild_id)
                        continue
                    
                    channel = guild.get_channel(session.channel_id)
                    if not channel:
                        guild_ids_to_remove.append(guild_id)
                        continue
                    
                    try:
                        message = await channel.fetch_message(session.message_id)
                        embed = TimerDisplayEmbed(
                            session.team1,
                            session.team2,
                            session.time_remaining,
                            is_running=session.is_running
                        )
                        await message.edit(embed=embed)
                    except discord.NotFound:
                        guild_ids_to_remove.append(guild_id)
                        continue
                    
                    # Check if timer finished
                    if session.is_finished:
                        # Auto-end match if configured
                        timer_config = await self.config_manager.get_timer_config(guild_id)
                        if timer_config.get('auto_end', True):
                            await self._end_match(guild, session)
                            guild_ids_to_remove.append(guild_id)
                
                except Exception as e:
                    logger.error(f"Error updating timer for guild {guild_id}: {e}")
                    guild_ids_to_remove.append(guild_id)
            
            # Clean up finished timers
            for guild_id in guild_ids_to_remove:
                if guild_id in self.active_timers:
                    del self.active_timers[guild_id]
        
        except Exception as e:
            logger.error(f"Error in timer loop: {e}")

    @timer_loop.before_loop
    async def before_timer_loop(self):
        """Wait for bot to be ready before starting loop"""
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="timer",
        description="Start a match timer (time-based game)"
    )
    async def start_timer(self, interaction: discord.Interaction):
        """Start a new match timer"""
        
        await interaction.response.defer(ephemeral=True)
        
        # Check if already has active timer
        if interaction.guild_id in self.active_timers:
            await interaction.followup.send(
                "⏱️ Timer already running! Use `/timer-stop` to end it.",
                ephemeral=True
            )
            return
        
        try:
            # Get active season
            season = await queries.get_active_season(self.supabase)
            if not season:
                await interaction.followup.send("❌ No active season found", ephemeral=True)
                return
            
            # Get all teams for this season
            teams = await queries.get_all_teams_for_season(self.supabase, season['id'])
            if not teams:
                await interaction.followup.send("❌ No teams found for this season", ephemeral=True)
                return
            
            # Show team selection
            teams_data = [
                {
                    'code': t['code'],
                    'name': t['name'],
                    'emoji': t.get('emoji', '🏀'),
                    'id': t['id'],
                    'score': 0
                }
                for t in teams
            ]
            
            embed = discord.Embed(
                title="🏆 Select Teams for Timer",
                description="Click two team buttons to start the timer",
                color=discord.Color.blue()
            )
            
            selection_view = TeamSelectionForTimerView(teams_data, self._on_teams_selected)
            await interaction.followup.send(embed=embed, view=selection_view, ephemeral=True)
        
        except Exception as e:
            logger.error(f"Error starting timer: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    async def _on_teams_selected(self, interaction: discord.Interaction, selected_teams: list):
        """Handle team selection for timer"""
        
        try:
            await interaction.response.defer()
            
            team1 = selected_teams[0]
            team2 = selected_teams[1]
            
            # Get timer config
            timer_config = await self.config_manager.get_timer_config(interaction.guild_id)
            duration = timer_config.get('duration_seconds', 600)  # Default 10 minutes
            
            # Create timer session
            session = TimerSession(
                message_id=0,  # Will be set after sending message
                channel_id=interaction.channel_id,
                guild_id=interaction.guild_id,
                team1=team1,
                team2=team2,
                duration_seconds=duration
            )
            
            # Create embed
            embed = TimerDisplayEmbed(team1, team2, duration, is_running=True)
            
            # Create views
            async def on_pause(pause_interaction: discord.Interaction):
                session.pause()
                await pause_interaction.response.defer()
            
            async def on_resume(resume_interaction: discord.Interaction):
                session.resume()
                await resume_interaction.response.defer()
            
            async def on_stop(stop_interaction: discord.Interaction):
                async def on_confirm(confirm_interaction: discord.Interaction):
                    await self._end_match(interaction.guild, session)
                    del self.active_timers[interaction.guild_id]
                
                async def on_cancel(cancel_interaction: discord.Interaction):
                    await cancel_interaction.response.defer()
                
                confirm_view = TimerConfirmView(on_confirm, on_cancel)
                await stop_interaction.response.send_message(
                    "⚠️ Are you sure you want to end this match?",
                    view=confirm_view,
                    ephemeral=True
                )
            
            timer_control_view = TimerControlView(on_pause, on_resume, on_stop)
            
            # Send timer message
            timer_message = await interaction.channel.send(
                embed=embed,
                view=timer_control_view
            )
            
            session.message_id = timer_message.id
            self.active_timers[interaction.guild_id] = session
            
            logger.info(f"✅ Timer started: {team1['name']} vs {team2['name']} in guild {interaction.guild_id}")
        
        except Exception as e:
            logger.error(f"Error selecting teams: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    async def _end_match(self, guild: discord.Guild, session: TimerSession):
        """End match and display results"""
        
        try:
            channel = guild.get_channel(session.channel_id)
            if not channel:
                return
            
            # Get final message
            try:
                message = await channel.fetch_message(session.message_id)
                
                # Create end embed
                embed = TimerDisplayEmbed.create_end_embed(
                    session.team1,
                    session.team2,
                    session.elapsed_seconds
                )
                
                # Edit with final state
                await message.edit(
                    embed=embed,
                    view=None  # Remove buttons
                )
            except discord.NotFound:
                pass
            
            # Send summary to channel
            summary_embed = discord.Embed(
                title="🏁 Match Summary",
                description=f"Timer completed",
                color=discord.Color.gold()
            )
            
            summary_embed.add_field(
                name=f"🥇 {session.team1['name']}",
                value=f"**{session.team1['score']}** pts",
                inline=True
            )
            
            summary_embed.add_field(
                name="Final",
                value=f"{session.elapsed_seconds // 60}:{session.elapsed_seconds % 60:02d}",
                inline=True
            )
            
            summary_embed.add_field(
                name=f"{session.team2['name']} 🥈",
                value=f"**{session.team2['score']}** pts",
                inline=True
            )
            
            summary_embed.set_footer(text="Use `/report-stats` to record individual player statistics")
            
            await channel.send(embed=summary_embed)
            
            logger.info(f"✅ Match ended in guild {session.guild_id}")
        
        except Exception as e:
            logger.error(f"Error ending match: {e}")

    @app_commands.command(
        name="timer-config",
        description="Configure timer settings for this server"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def configure_timer(
        self,
        interaction: discord.Interaction,
        duration_minutes: int = 10,
        auto_end: bool = True
    ):
        """Configure timer settings"""
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            await self.config_manager.set_timer_config(
                interaction.guild_id,
                duration_seconds=duration_minutes * 60,
                auto_end=auto_end
            )
            
            embed = discord.Embed(
                title="⏱️ Timer Configuration Updated",
                color=discord.Color.green()
            )
            
            embed.add_field(name="Duration", value=f"{duration_minutes} minutes", inline=False)
            embed.add_field(name="Auto-End", value="✅ Enabled" if auto_end else "❌ Disabled", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        except Exception as e:
            logger.error(f"Error configuring timer: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="timer-stop",
        description="Stop the current match timer"
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    async def stop_timer(self, interaction: discord.Interaction):
        """Stop the current timer"""
        
        if interaction.guild_id not in self.active_timers:
            await interaction.response.send_message(
                "❌ No active timer found",
                ephemeral=True
            )
            return
        
        try:
            await interaction.response.defer(ephemeral=True)
            
            session = self.active_timers[interaction.guild_id]
            await self._end_match(interaction.guild, session)
            del self.active_timers[interaction.guild_id]
            
            await interaction.followup.send("✅ Timer stopped", ephemeral=True)
        
        except Exception as e:
            logger.error(f"Error stopping timer: {e}")
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(TimerCog(bot))
    logger.info("✅ TimerCog loaded")
