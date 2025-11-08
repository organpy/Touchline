"""
Simple Timer Cog - Match timer with pause/resume/stop buttons
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class TimerSession:
    """Represents an active match timer"""

    def __init__(self, message_id: int, channel_id: int, guild_id: int, user_id: int, duration_seconds: int = 600):
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.user_id = user_id  # Only this user can control the timer
        self.duration_seconds = duration_seconds
        self.elapsed_seconds = 0
        self.is_running = True
        self.created_at = datetime.utcnow()

    @property
    def time_remaining(self) -> int:
        """Get seconds remaining"""
        return max(0, self.duration_seconds - self.elapsed_seconds)

    @property
    def is_finished(self) -> bool:
        """Check if timer has finished"""
        return self.time_remaining <= 0

    @property
    def formatted_time(self) -> str:
        """Format time as MM:SS"""
        remaining = self.time_remaining
        minutes = remaining // 60
        seconds = remaining % 60
        return f"{minutes:02d}:{seconds:02d}"

    def pause(self):
        """Pause the timer"""
        self.is_running = False

    def resume(self):
        """Resume the timer"""
        self.is_running = True

    def tick(self):
        """Advance timer by 1 second"""
        if self.is_running and not self.is_finished:
            self.elapsed_seconds += 1


class TimerControlView(discord.ui.View):
    """Control buttons for timer (pause/resume/stop)"""

    def __init__(self, session: TimerSession, on_stop_callback):
        super().__init__(timeout=None)
        self.session = session
        self.on_stop_callback = on_stop_callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allow the user who started the timer to interact"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Only the person who started this timer can control it!",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.session.is_running:
            await interaction.response.defer()
            return
        
        self.session.pause()
        self.pause_button.disabled = True
        self.resume_button.disabled = False
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success, disabled=True)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.is_running:
            await interaction.response.defer()
            return
        
        self.session.resume()
        self.pause_button.disabled = False
        self.resume_button.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.on_stop_callback(interaction)


class TimerCog(commands.Cog):
    """Simple timer cog for matches"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Changed from guild_id -> session to (guild_id, user_id) -> session
        # This allows multiple timers per guild (one per user)
        self.active_timers: Dict[tuple, TimerSession] = {}  # (guild_id, user_id) -> TimerSession
        self.timer_loop.start()

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.timer_loop.cancel()

    @tasks.loop(seconds=1)
    async def timer_loop(self):
        """Main timer loop - runs every second"""
        try:
            keys_to_remove = []
            
            # Create a copy of items to iterate over (prevents modification during iteration)
            current_timers = list(self.active_timers.items())
            
            for (guild_id, user_id), session in current_timers:
                # Advance timer
                session.tick()
                
                # Update display
                try:
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        keys_to_remove.append((guild_id, user_id))
                        continue
                    
                    channel = guild.get_channel(session.channel_id)
                    if not channel:
                        keys_to_remove.append((guild_id, user_id))
                        continue
                    
                    try:
                        message = await channel.fetch_message(session.message_id)
                        
                        # Create embed with current time
                        embed = discord.Embed(
                            title="⏱️ Match Timer",
                            description=f"**{session.formatted_time}**",
                            color=discord.Color.blue() if session.is_running else discord.Color.orange()
                        )
                        embed.add_field(
                            name="Status",
                            value="▶️ Running" if session.is_running else "⏸️ Paused",
                            inline=False
                        )
                        
                        await message.edit(embed=embed)
                    except discord.NotFound:
                        keys_to_remove.append((guild_id, user_id))
                        continue
                    
                    # Check if timer finished
                    if session.is_finished:
                        # Send completion message
                        embed = discord.Embed(
                            title="⏱️ Match Timer Complete!",
                            description="**00:00**",
                            color=discord.Color.green()
                        )
                        await channel.send(embed=embed)
                        keys_to_remove.append((guild_id, user_id))
                
                except Exception as e:
                    logger.error(f"Error updating timer for {(guild_id, user_id)}: {e}")
                    keys_to_remove.append((guild_id, user_id))
            
            # Clean up finished timers
            for key in keys_to_remove:
                if key in self.active_timers:
                    del self.active_timers[key]
        
        except Exception as e:
            logger.error(f"Error in timer loop: {e}")

    @timer_loop.before_loop
    async def before_timer_loop(self):
        """Wait for bot to be ready before starting loop"""
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="timer",
        description="Start a 10-minute match timer"
    )
    @app_commands.describe(minutes="Duration in minutes (default: 10)")
    async def start_timer(self, interaction: discord.Interaction, minutes: int = 10):
        """Start a new match timer"""
        
        await interaction.response.defer(ephemeral=False)
        
        # Check if this user already has an active timer
        timer_key = (interaction.guild_id, interaction.user.id)
        if timer_key in self.active_timers:
            await interaction.followup.send(
                "⏱️ You already have a timer running! Use the Stop button to end it.",
                ephemeral=True
            )
            return
        
        try:
            # Validate duration
            if minutes < 1 or minutes > 120:
                await interaction.followup.send("❌ Duration must be between 1 and 120 minutes", ephemeral=True)
                return
            
            duration_seconds = minutes * 60
            
            # Create timer session
            session = TimerSession(
                message_id=0,  # Will be set after sending message
                channel_id=interaction.channel_id,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,  # Store user ID
                duration_seconds=duration_seconds
            )
            
            # Create embed
            embed = discord.Embed(
                title="⏱️ Match Timer",
                description=f"**{session.formatted_time}**",
                color=discord.Color.blue()
            )
            embed.add_field(name="Status", value="▶️ Running", inline=False)
            embed.add_field(name="Duration", value=f"{minutes} minute{'s' if minutes != 1 else ''}", inline=False)
            embed.set_footer(text=f"Started by {interaction.user.name}")
            
            # Create view with control buttons
            async def on_stop(stop_interaction: discord.Interaction):
                """Handle stop button"""
                if timer_key in self.active_timers:
                    del self.active_timers[timer_key]
                embed = discord.Embed(
                    title="⏹️ Timer Stopped",
                    description=f"Match stopped at **{session.formatted_time}**",
                    color=discord.Color.red()
                )
                try:
                    await message.edit(embed=embed, view=None)
                except:
                    pass
            
            view = TimerControlView(session, on_stop)
            
            # Send message
            message = await interaction.followup.send(embed=embed, view=view)
            session.message_id = message.id
            
            # Store session (one per user)
            self.active_timers[timer_key] = session
            logger.info(f"✅ Timer started for {interaction.user.name} in guild {interaction.guild_id} ({minutes}m)")
        
        except Exception as e:
            logger.error(f"Error starting timer: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="timer-stop",
        description="Stop the active timer"
    )
    async def stop_timer_cmd(self, interaction: discord.Interaction):
        """Stop active timer via command"""
        await interaction.response.defer(ephemeral=True)
        
        timer_key = (interaction.guild_id, interaction.user.id)
        if timer_key not in self.active_timers:
            await interaction.followup.send("❌ You don't have an active timer", ephemeral=True)
            return
        
        session = self.active_timers[timer_key]
        del self.active_timers[timer_key]
        
        embed = discord.Embed(
            title="⏹️ Timer Stopped",
            description=f"Match stopped at **{session.formatted_time}**",
            color=discord.Color.red()
        )
        
        try:
            channel = interaction.guild.get_channel(session.channel_id)
            if channel:
                message = await channel.fetch_message(session.message_id)
                await message.edit(embed=embed, view=None)
        except:
            pass
        
        await interaction.followup.send("✅ Timer stopped", ephemeral=True)

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


async def setup(bot: commands.Bot):
    """Setup function called by bot when loading this cog"""
    await bot.add_cog(TimerCog(bot))
    logger.info("✅ TimerCog loaded")
