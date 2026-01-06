"""
Simple Timer Cog - Match timer with pause/resume/stop buttons
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
from datetime import datetime
from typing import Optional, Dict

from utils.rate_limiter import timer_requests_limiter
from utils.error_embeds import rate_limit_embed

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
        self.message = None  # Cache the message object to avoid API calls
        self.edit_count = 0  # Track message edits to avoid hitting Discord limits
        self.last_error_time = None  # Track when last error occurred

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

    # Limits to prevent abuse and performance issues
    MAX_CONCURRENT_TIMERS = 50  # Max timers per guild
    MAX_DURATION_MINUTES = 120  # Max timer duration
    MIN_DURATION_MINUTES = 1    # Min timer duration
    MAX_MESSAGE_EDITS = 9000    # Discord's rough edit limit per message

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Changed from guild_id -> session to (guild_id, user_id) -> session
        # This allows multiple timers per guild (one per user)
        self.active_timers: Dict[tuple, TimerSession] = {}  # (guild_id, user_id) -> TimerSession
        self.timer_loop.start()

    def cog_unload(self):
        """Clean up when cog is unloaded - stop all timers gracefully"""
        logger.info("🛑 Stopping all active timers due to cog unload...")
        
        # Cancel the loop
        self.timer_loop.cancel()
        
        # Clear all active timers (they will be lost, but that's acceptable)
        timer_count = len(self.active_timers)
        self.active_timers.clear()
        
        logger.info(f"✅ Cleaned up {timer_count} active timers")

    @app_commands.command(
        name="timer-emergency-stop",
        description="[ADMIN] Emergency stop all timers in this server"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def emergency_stop_all(self, interaction: discord.Interaction):
        """Emergency stop all timers in the guild - admin only"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild_id
        guild_timers = [(k, v) for k, v in self.active_timers.items() if k[0] == guild_id]
        
        if not guild_timers:
            await interaction.followup.send("✅ No active timers to stop", ephemeral=True)
            return
        
        stopped_count = 0
        for (timer_key, session) in guild_timers:
            try:
                # Try to update the message to show stopped state
                if session.message:
                    embed = discord.Embed(
                        title="⏹️ Timer Emergency Stopped",
                        description=f"**{session.formatted_time}**",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Reason", value="Emergency stop by admin", inline=False)
                    await session.message.edit(embed=embed, view=None)
            except Exception as e:
                logger.error(f"Error updating emergency stopped timer {timer_key}: {e}")
            
            # Remove from active timers
            del self.active_timers[timer_key]
            stopped_count += 1
        
        embed = discord.Embed(
            title="🚨 Emergency Timer Stop",
            description=f"Stopped {stopped_count} timer{'s' if stopped_count != 1 else ''}",
            color=discord.Color.red()
        )
        embed.set_footer(text="All timers in this server have been stopped")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"🚨 Admin {interaction.user} emergency stopped {stopped_count} timers in guild {guild_id}")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle app command errors for this cog"""
        if isinstance(error, app_commands.MissingPermissions):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need **Administrator** permissions to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(
                name="Required Permission",
                value="🛡️ Server Administrator",
                inline=False
            )
            embed.set_footer(text="Contact a server admin if you need access to this feature")
            
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.Forbidden:
                # Bot can't send messages - this is a bigger issue
                logger.error(f"Bot lacks permission to send error messages in guild {interaction.guild_id}")
            except Exception as e:
                logger.error(f"Error sending permission error message: {e}")
        else:
            # Re-raise other errors to be handled by global error handler
            raise error

    @app_commands.command(
        name="timer-status",
        description="[MEMBERS] Check timer status for this server"
    )
    async def timer_status(self, interaction: discord.Interaction):
        """Check how many timers are active in this server"""
        await interaction.response.defer(ephemeral=True)
        
        guild_id = interaction.guild_id
        guild_timers = [(k, v) for k, v in self.active_timers.items() if k[0] == guild_id]
        
        embed = discord.Embed(
            title="⏱️ Timer Status",
            color=discord.Color.blue()
        )
        
        if not guild_timers:
            embed.description = "No active timers in this server"
            embed.color = discord.Color.green()
        else:
            embed.add_field(
                name="Active Timers",
                value=f"{len(guild_timers)} timer{'s' if len(guild_timers) != 1 else ''}",
                inline=False
            )
            
            # List timer owners (without pinging them)
            timer_list = []
            for (guild_id, user_id), session in guild_timers[:5]:  # Show max 5
                try:
                    user = self.bot.get_user(user_id)
                    username = user.name if user else f"User {user_id}"
                    timer_list.append(f"• {username}: {session.formatted_time}")
                except:
                    timer_list.append(f"• User {user_id}: {session.formatted_time}")
            
            if timer_list:
                embed.add_field(
                    name="Timer Details",
                    value="\n".join(timer_list),
                    inline=False
                )
            
            if len(guild_timers) > 5:
                embed.set_footer(text=f"And {len(guild_timers) - 5} more timers...")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(seconds=1)
    async def timer_loop(self):
        """Main timer loop - runs every second with optimized API calls and safety limits"""
        try:
            # Safety check: don't process too many timers at once to prevent blocking
            if len(self.active_timers) > self.MAX_CONCURRENT_TIMERS * 2:  # Allow some overflow
                logger.warning(f"Too many active timers ({len(self.active_timers)}), cleaning up old ones")
                # Remove timers older than 24 hours as emergency cleanup
                cutoff_time = datetime.utcnow().timestamp() - (24 * 60 * 60)
                to_remove = []
                for key, session in self.active_timers.items():
                    if session.created_at.timestamp() < cutoff_time:
                        to_remove.append(key)
                
                for key in to_remove:
                    del self.active_timers[key]
                    logger.info(f"Emergency cleanup: removed old timer {key}")
            
            keys_to_remove = []
            
            # Create a copy of items to iterate over (prevents modification during iteration)
            current_timers = list(self.active_timers.items())
            
            for (guild_id, user_id), session in current_timers:
                # Advance timer
                session.tick()
                
                # Update display (only if timer is still running and not just finished)
                if not session.is_finished:
                    try:
                        # Cache message object to avoid repeated API calls
                        if session.message is None:
                            guild = self.bot.get_guild(guild_id)
                            if not guild:
                                keys_to_remove.append((guild_id, user_id))
                                continue
                            
                            channel = guild.get_channel(session.channel_id)
                            if not channel:
                                keys_to_remove.append((guild_id, user_id))
                                continue
                            
                            try:
                                session.message = await channel.fetch_message(session.message_id)
                            except discord.NotFound:
                                keys_to_remove.append((guild_id, user_id))
                                continue
                        
                        # Check edit limit to prevent Discord API issues
                        if session.edit_count >= self.MAX_MESSAGE_EDITS:
                            logger.warning(f"Timer {session.message_id} hit edit limit ({self.MAX_MESSAGE_EDITS}), stopping updates")
                            keys_to_remove.append((guild_id, user_id))
                            continue
                        
                        if should_update:
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
                            
                            try:
                                await session.message.edit(embed=embed)
                                session.edit_count += 1
                            except discord.Forbidden:
                                # Bot lost permissions to edit message
                                logger.error(f"Lost permission to edit timer message {session.message_id}")
                                keys_to_remove.append((guild_id, user_id))
                                continue
                            except discord.HTTPException as e:
                                # Rate limited or other API error
                                if e.status == 429:  # Rate limited
                                    logger.warning(f"Rate limited editing timer message {session.message_id}")
                                    # Don't remove timer, just skip this update
                                    continue
                                else:
                                    logger.error(f"HTTP error editing timer message {session.message_id}: {e}")
                                    keys_to_remove.append((guild_id, user_id))
                                    continue
                    
                    except Exception as e:
                        logger.error(f"Error updating timer for {(guild_id, user_id)}: {e}")
                        keys_to_remove.append((guild_id, user_id))
                        continue
                
                # Check if timer finished
                if session.is_finished:
                    # Send completion message
                    try:
                        if session.message:
                            channel = session.message.channel
                        else:
                            guild = self.bot.get_guild(guild_id)
                            if guild:
                                channel = guild.get_channel(session.channel_id)
                            else:
                                channel = None
                        
                        if channel:
                            embed = discord.Embed(
                                title="⏱️ Match Timer Complete!",
                                description="**00:00**",
                                color=discord.Color.green()
                            )
                            try:
                                await channel.send(embed=embed)
                            except discord.Forbidden:
                                logger.error(f"Lost permission to send completion message in channel {session.channel_id}")
                            except discord.HTTPException as e:
                                if e.status == 429:
                                    logger.warning(f"Rate limited sending completion message in channel {session.channel_id}")
                                else:
                                    logger.error(f"HTTP error sending completion message: {e}")
                        
                        keys_to_remove.append((guild_id, user_id))
                    except Exception as e:
                        logger.error(f"Error sending completion for {(guild_id, user_id)}: {e}")
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
        description="[MEMBERS] Start a match timer"
    )
    @app_commands.describe(minutes="Duration in minutes (default: 10)")
    async def start_timer(self, interaction: discord.Interaction, minutes: int = 10):
        """Start a new match timer"""
        
        # Rate limit check (1 timer start per 5 seconds per guild)
        guild_key = f"timer_start_{interaction.guild_id}"
        if not timer_requests_limiter.allow(guild_key):
            retry_after = timer_requests_limiter.get_retry_after(guild_key)
            embed = rate_limit_embed(int(retry_after) + 1)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Check concurrent timer limit per guild
        guild_timers = [k for k in self.active_timers.keys() if k[0] == interaction.guild_id]
        if len(guild_timers) >= self.MAX_CONCURRENT_TIMERS:
            await interaction.followup.send(
                f"❌ Too many active timers in this server! Maximum: {self.MAX_CONCURRENT_TIMERS}",
                ephemeral=True
            )
            return
        
        # Check if this user already has an active timer
        timer_key = (interaction.guild_id, interaction.user.id)
        if timer_key in self.active_timers:
            await interaction.followup.send(
                "⏱️ You already have a timer running! Use the Stop button to end it.",
                ephemeral=True
            )
            return
        
        try:
            # Validate duration with constants
            if not isinstance(minutes, int) or minutes < self.MIN_DURATION_MINUTES or minutes > self.MAX_DURATION_MINUTES:
                await interaction.followup.send(
                    f"❌ Duration must be between {self.MIN_DURATION_MINUTES} and {self.MAX_DURATION_MINUTES} minutes",
                    ephemeral=True
                )
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
                    if session.message:
                        await session.message.edit(embed=embed, view=None)
                    else:
                        await message.edit(embed=embed, view=None)
                except:
                    pass
            
            view = TimerControlView(session, on_stop)
            
            # Send message
            message = await interaction.followup.send(embed=embed, view=view)
            session.message_id = message.id
            session.message = message  # Cache the message object
            
            # Store session (one per user)
            self.active_timers[timer_key] = session
            logger.info(f"✅ Timer started for {interaction.user.name} in guild {interaction.guild_id} ({minutes}m)")
        
        except Exception as e:
            logger.error(f"Error starting timer: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="timer-stop",
        description="[MEMBERS] Stop the active timer"
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
            if session.message:
                await session.message.edit(embed=embed, view=None)
            else:
                channel = interaction.guild.get_channel(session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    await message.edit(embed=embed, view=None)
        except:
            pass
        
        await interaction.followup.send("✅ Timer stopped", ephemeral=True)

    @app_commands.command(
        name="timer-config",
        description="[ADMIN] Configure timer settings for this server"
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
    try:
        # Validate that required permissions are available
        if not hasattr(bot, 'get_guild'):
            raise RuntimeError("Bot missing required methods for timer functionality")
        
        await bot.add_cog(TimerCog(bot))
        logger.info("✅ TimerCog loaded successfully")
        logger.info(f"📊 Timer limits: Max {TimerCog.MAX_CONCURRENT_TIMERS} concurrent timers per guild")
        
    except Exception as e:
        logger.error(f"❌ Failed to load TimerCog: {e}", exc_info=True)
        raise
