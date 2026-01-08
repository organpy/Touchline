"""
Simple Timer Cog - Match timer with pause/resume/stop buttons
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Dict

from utils.rate_limiter import timer_requests_limiter
from utils.error_embeds import rate_limit_embed

logger = logging.getLogger(__name__)


@dataclass
class TimerGuildSettings:
    max_concurrent_timers: int = 50
    min_duration_minutes: int = 1
    max_duration_minutes: int = 120
    update_interval_seconds: int = 5


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
        embed = discord.Embed(
            title="⏱️ Match Timer",
            description=f"**{self.session.formatted_time}**",
            color=discord.Color.orange()
        )
        embed.add_field(name="Status", value="⏸️ Paused", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success, disabled=True)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.is_running:
            await interaction.response.defer()
            return
        
        self.session.resume()
        self.pause_button.disabled = False
        self.resume_button.disabled = True
        embed = discord.Embed(
            title="⏱️ Match Timer",
            description=f"**{self.session.formatted_time}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value="▶️ Running", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.on_stop_callback(interaction)


class TimerCog(commands.Cog):
    """Simple timer cog for matches"""

    # Limits to prevent abuse and performance issues
    MAX_CONCURRENT_TIMERS = 50  # Default max timers per guild
    MAX_DURATION_MINUTES = 120  # Default max timer duration
    MIN_DURATION_MINUTES = 1    # Default min timer duration
    MAX_MESSAGE_EDITS = 9000    # Discord's rough edit limit per message

    # Hard caps (safety rails for GUI editing)
    MAX_CONCURRENT_TIMERS_CAP = 200
    MIN_UPDATE_INTERVAL_SECONDS = 3
    MAX_UPDATE_INTERVAL_SECONDS = 30
    MAX_DURATION_MINUTES_CAP = 360
    MIN_DURATION_MINUTES_CAP = 1

    # Admin configuration is exposed as a single GUI command: /timer-config

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Active timers stored as: (guild_id, user_id) -> TimerSession
        self.active_timers: Dict[tuple[int, int], TimerSession] = {}
        self.guild_settings: Dict[int, TimerGuildSettings] = {}

        if not self.timer_loop.is_running():
            self.timer_loop.start()

    def _get_guild_settings(self, guild_id: int) -> TimerGuildSettings:
        settings = self.guild_settings.get(guild_id)
        if settings is None:
            settings = TimerGuildSettings(
                max_concurrent_timers=self.MAX_CONCURRENT_TIMERS,
                min_duration_minutes=self.MIN_DURATION_MINUTES,
                max_duration_minutes=self.MAX_DURATION_MINUTES,
                update_interval_seconds=5,
            )
            self.guild_settings[guild_id] = settings
        return settings

    def _apply_guild_settings(
        self,
        guild_id: int,
        *,
        max_concurrent_timers: int,
        min_duration_minutes: int,
        max_duration_minutes: int,
        update_interval_seconds: int,
    ) -> TimerGuildSettings:
        max_concurrent_timers = int(max_concurrent_timers)
        min_duration_minutes = int(min_duration_minutes)
        max_duration_minutes = int(max_duration_minutes)
        update_interval_seconds = int(update_interval_seconds)

        if max_concurrent_timers < 1 or max_concurrent_timers > self.MAX_CONCURRENT_TIMERS_CAP:
            raise ValueError(f"Max concurrent timers must be 1–{self.MAX_CONCURRENT_TIMERS_CAP}.")

        if min_duration_minutes < self.MIN_DURATION_MINUTES_CAP or min_duration_minutes > self.MAX_DURATION_MINUTES_CAP:
            raise ValueError(f"Min duration must be {self.MIN_DURATION_MINUTES_CAP}–{self.MAX_DURATION_MINUTES_CAP} minutes.")

        if max_duration_minutes < self.MIN_DURATION_MINUTES_CAP or max_duration_minutes > self.MAX_DURATION_MINUTES_CAP:
            raise ValueError(f"Max duration must be {self.MIN_DURATION_MINUTES_CAP}–{self.MAX_DURATION_MINUTES_CAP} minutes.")

        if min_duration_minutes > max_duration_minutes:
            raise ValueError("Min duration cannot be greater than max duration.")

        if update_interval_seconds < self.MIN_UPDATE_INTERVAL_SECONDS or update_interval_seconds > self.MAX_UPDATE_INTERVAL_SECONDS:
            raise ValueError(
                f"Update interval must be {self.MIN_UPDATE_INTERVAL_SECONDS}–{self.MAX_UPDATE_INTERVAL_SECONDS} seconds."
            )

        settings = self._get_guild_settings(guild_id)
        settings.max_concurrent_timers = max_concurrent_timers
        settings.min_duration_minutes = min_duration_minutes
        settings.max_duration_minutes = max_duration_minutes
        settings.update_interval_seconds = update_interval_seconds
        return settings

    def cog_unload(self):
        """Clean up when cog is unloaded - stop all timers gracefully"""
        logger.info("🛑 Stopping all active timers due to cog unload...")
        
        # Cancel the loop
        self.timer_loop.cancel()
        
        # Clear all active timers (they will be lost, but that's acceptable)
        timer_count = len(self.active_timers)
        self.active_timers.clear()
        
        logger.info(f"✅ Cleaned up {timer_count} active timers")

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

    def _build_dashboard_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⏱️ Timer Control Panel",
            description=(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Admin timer controls for this server.\n"
                "Use the buttons below to view status, settings, or stop all timers.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0x2B2D31,
        )
        embed.add_field(name="📊 Status", value="> View active timers", inline=True)
        embed.add_field(name="🛑 Stop Timer", value="> Stop a specific user", inline=True)
        embed.add_field(name="⚙️ Settings", value="> View/edit safely", inline=True)
        embed.add_field(name="🚨 Emergency Stop", value="> Stop all timers", inline=True)
        embed.set_footer(text="/timer-config is admin-only")
        return embed

    def _build_status_embed(self, guild_id: int) -> discord.Embed:
        guild_timers = [(k, v) for k, v in self.active_timers.items() if k[0] == guild_id]
        embed = discord.Embed(title="⏱️ Timer Status", color=discord.Color.blue())

        if not guild_timers:
            embed.description = "No active timers in this server"
            embed.color = discord.Color.green()
            return embed

        embed.add_field(
            name="Active Timers",
            value=f"{len(guild_timers)} timer{'s' if len(guild_timers) != 1 else ''}",
            inline=False,
        )

        timer_list = []
        for (_, user_id), session in guild_timers[:10]:  # Show max 10
            user = self.bot.get_user(user_id)
            username = user.name if user else f"User {user_id}"
            timer_list.append(f"• {username}: {session.formatted_time}{' (paused)' if not session.is_running else ''}")

        if timer_list:
            embed.add_field(name="Timer Details", value="\n".join(timer_list), inline=False)

        if len(guild_timers) > 10:
            embed.set_footer(text=f"And {len(guild_timers) - 10} more timers...")

        return embed

    def _build_settings_embed(self, guild_id: int) -> discord.Embed:
        settings = self._get_guild_settings(guild_id)
        guild_timer_count = sum(1 for (g, _u) in self.active_timers.keys() if g == guild_id)

        embed = discord.Embed(
            title="⚙️ Timer Settings",
            description="Current timer configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Max Concurrent Timers", value=str(settings.max_concurrent_timers), inline=True)
        embed.add_field(name="Active Timers", value=str(guild_timer_count), inline=True)
        embed.add_field(
            name="Duration Range",
            value=f"{settings.min_duration_minutes}–{settings.max_duration_minutes} minutes",
            inline=True,
        )
        embed.add_field(name="Update Interval", value=f"{settings.update_interval_seconds} seconds (running)", inline=True)
        embed.add_field(name="Message Caching", value="Enabled", inline=True)
        embed.add_field(name="Edit Safety Limit", value=str(self.MAX_MESSAGE_EDITS), inline=True)
        embed.set_footer(text="Timers are in-memory only (lost on restart)")
        return embed

    async def _emergency_stop_guild(self, guild_id: int, requested_by: discord.abc.User) -> int:
        guild_timers = [(k, v) for k, v in self.active_timers.items() if k[0] == guild_id]
        if not guild_timers:
            return 0

        stopped_count = 0
        for (timer_key, session) in guild_timers:
            try:
                if session.message:
                    embed = discord.Embed(
                        title="⏹️ Timer Emergency Stopped",
                        description=f"**{session.formatted_time}**",
                        color=discord.Color.red(),
                    )
                    embed.add_field(name="Reason", value="Emergency stop by admin", inline=False)
                    await session.message.edit(embed=embed, view=None)
            except Exception as e:
                logger.error(f"Error updating emergency stopped timer {timer_key}: {e}")

            if timer_key in self.active_timers:
                del self.active_timers[timer_key]
                stopped_count += 1

        logger.info(
            "🚨 Admin %s emergency stopped %s timers in guild %s",
            getattr(requested_by, "id", "unknown"),
            stopped_count,
            guild_id,
        )
        return stopped_count

    @app_commands.command(
        name="timer-config",
        description="[ADMIN] Timer control panel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def timer_config_dashboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used inside a Discord server.",
                ephemeral=True,
            )
            return

        view = TimerConfigDashboardView(self, interaction.guild_id)
        await interaction.response.send_message(embed=self._build_dashboard_embed(), view=view, ephemeral=True)
        try:
            view.original_message = await interaction.original_response()
        except Exception:
            pass

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
                    logger.info(f"Emergency cleanup: removed old timer(s) {key}")
            
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

                        # Only edit the message every N seconds while running (per-guild setting)
                        update_interval = self._get_guild_settings(guild_id).update_interval_seconds
                        update_interval = max(self.MIN_UPDATE_INTERVAL_SECONDS, min(self.MAX_UPDATE_INTERVAL_SECONDS, int(update_interval)))
                        should_update = session.is_running and (session.elapsed_seconds % update_interval == 0)
                        
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

        if not interaction.guild_id:
            await interaction.response.send_message("❌ This command can only be used inside a server.", ephemeral=True)
            return

        settings = self._get_guild_settings(interaction.guild_id)
        
        # Rate limit check (1 timer start per 5 seconds per user)
        limiter_key = f"timer_start_{interaction.guild_id}_{interaction.user.id}"
        if not timer_requests_limiter.allow(limiter_key):
            retry_after = timer_requests_limiter.get_retry_after(limiter_key)
            embed = rate_limit_embed(int(retry_after) + 1)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        
        # Check concurrent timer limit per guild
        guild_timers = [k for k in self.active_timers.keys() if k[0] == interaction.guild_id]
        if len(guild_timers) >= settings.max_concurrent_timers:
            await interaction.followup.send(
                f"❌ Too many active timers in this server! Maximum: {settings.max_concurrent_timers}",
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
            if not isinstance(minutes, int) or minutes < settings.min_duration_minutes or minutes > settings.max_duration_minutes:
                await interaction.followup.send(
                    f"❌ Duration must be between {settings.min_duration_minutes} and {settings.max_duration_minutes} minutes",
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



class TimerConfigDashboardView(discord.ui.View):
    """GUI dashboard for /timer-config (admin-only)."""

    def __init__(self, cog: TimerCog, guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.original_message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.administrator:
            try:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permissions to use /timer-config.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    async def _safe_edit(self, interaction: discord.Interaction, *, embed: discord.Embed, view: Optional[discord.ui.View] = None) -> None:
        try:
            await interaction.response.edit_message(embed=embed, view=view or self)
        except discord.NotFound:
            return
        except Exception as exc:
            logger.error("Failed to edit timer-config dashboard: %s", exc)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, row=0)
    async def status_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._safe_edit(interaction, embed=self.cog._build_status_embed(self.guild_id))

    @discord.ui.button(label="Stop Timer", style=discord.ButtonStyle.secondary, row=0)
    async def stop_timer_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        select_view = TimerStopByUserView(self.cog, self.guild_id)
        embed = discord.Embed(
            title="🛑 Stop A Timer",
            description="Select a user to stop their active timer.",
            color=discord.Color.orange(),
        )
        await self._safe_edit(interaction, embed=embed, view=select_view)
        try:
            select_view.original_message = await interaction.original_response()
        except Exception:
            pass

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, row=0)
    async def settings_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._safe_edit(interaction, embed=self.cog._build_settings_embed(self.guild_id))

    @discord.ui.button(label="Edit Settings", style=discord.ButtonStyle.primary, row=1)
    async def edit_settings_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        settings = self.cog._get_guild_settings(self.guild_id)
        modal = TimerSettingsModal(self.cog, self.guild_id, settings)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Emergency Stop", style=discord.ButtonStyle.danger, row=1)
    async def emergency_stop_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        confirm_view = TimerEmergencyStopConfirmView(self.cog, self.guild_id)
        embed = discord.Embed(
            title="🚨 Confirm Emergency Stop",
            description="This will stop **all** active timers in this server.\n\nAre you sure?",
            color=discord.Color.red(),
        )
        await self._safe_edit(interaction, embed=embed, view=confirm_view)
        try:
            confirm_view.original_message = await interaction.original_response()
        except Exception:
            pass

    @discord.ui.button(label="Home", style=discord.ButtonStyle.primary, row=1)
    async def home_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._safe_edit(interaction, embed=self.cog._build_dashboard_embed())

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=2)
    async def close_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="✅ Timer Config Closed",
            description="Timer control panel has been closed.",
            color=discord.Color.green(),
        )
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await self._safe_edit(interaction, embed=embed)
        self.stop()


class TimerEmergencyStopConfirmView(discord.ui.View):
    """Confirmation view for emergency-stop."""

    def __init__(self, cog: TimerCog, guild_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.original_message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.administrator:
            try:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permissions to do this.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    async def _safe_edit(self, interaction: discord.Interaction, *, embed: discord.Embed, view: Optional[discord.ui.View] = None) -> None:
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.NotFound:
            return
        except Exception as exc:
            logger.error("Failed to edit emergency-stop confirm view: %s", exc)

    @discord.ui.button(label="Confirm Stop", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        stopped_count = await self.cog._emergency_stop_guild(self.guild_id, interaction.user)
        if stopped_count == 0:
            embed = discord.Embed(
                title="✅ No Active Timers",
                description="There were no active timers to stop.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="🚨 Emergency Stop Complete",
                description=f"Stopped **{stopped_count}** timer{'s' if stopped_count != 1 else ''}.",
                color=discord.Color.red(),
            )
        await self._safe_edit(interaction, embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        dashboard = TimerConfigDashboardView(self.cog, self.guild_id)
        await self._safe_edit(interaction, embed=self.cog._build_dashboard_embed(), view=dashboard)
        try:
            dashboard.original_message = await interaction.original_response()
        except Exception:
            pass
        self.stop()


class TimerStopByUserView(discord.ui.View):
    """Select a user with an active timer and stop it."""

    def __init__(self, cog: TimerCog, guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.original_message: Optional[discord.Message] = None

        options: list[discord.SelectOption] = []
        guild_timers = [((g, u), s) for ((g, u), s) in cog.active_timers.items() if g == guild_id]

        for ((_, user_id), session) in guild_timers[:25]:
            user = cog.bot.get_user(user_id)
            username = user.name if user else f"User {user_id}"
            desc = f"{session.formatted_time}{' (paused)' if not session.is_running else ''}"
            options.append(discord.SelectOption(label=username[:100], description=desc[:100], value=str(user_id)))

        if not options:
            options.append(discord.SelectOption(label="No active timers", description="Nothing to stop", value="none"))

        self.select = discord.ui.Select(
            placeholder="Select a user...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self._on_select  # type: ignore[attr-defined]
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.administrator:
            try:
                await interaction.response.send_message(
                    "❌ You need **Administrator** permissions to do this.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        value = self.select.values[0]
        if value == "none":
            await interaction.response.edit_message(
                embed=discord.Embed(title="✅ No Active Timers", description="Nothing to stop.", color=discord.Color.green()),
                view=None,
            )
            self.stop()
            return

        user_id = int(value)
        timer_key = (self.guild_id, user_id)
        session = self.cog.active_timers.get(timer_key)
        if not session:
            await interaction.response.edit_message(
                embed=discord.Embed(title="⚠️ Not Found", description="That timer is no longer active.", color=discord.Color.orange()),
                view=None,
            )
            self.stop()
            return

        # Stop it
        del self.cog.active_timers[timer_key]
        try:
            if session.message:
                embed = discord.Embed(
                    title="⏹️ Timer Stopped",
                    description=f"Stopped at **{session.formatted_time}**",
                    color=discord.Color.red(),
                )
                await session.message.edit(embed=embed, view=None)
        except Exception:
            pass

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Timer Stopped",
                description=f"Stopped timer for <@{user_id}>.",
                color=discord.Color.green(),
            ),
            view=None,
        )
        self.stop()


class TimerSettingsModal(discord.ui.Modal):
    title = "Timer Settings"

    def __init__(self, cog: TimerCog, guild_id: int, settings: TimerGuildSettings):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id

        self.max_timers = discord.ui.TextInput(
            label="Max concurrent timers (1–200)",
            default=str(settings.max_concurrent_timers),
            required=True,
            max_length=4,
        )
        self.min_minutes = discord.ui.TextInput(
            label="Min duration minutes (1–360)",
            default=str(settings.min_duration_minutes),
            required=True,
            max_length=3,
        )
        self.max_minutes = discord.ui.TextInput(
            label="Max duration minutes (1–360)",
            default=str(settings.max_duration_minutes),
            required=True,
            max_length=3,
        )
        self.update_interval = discord.ui.TextInput(
            label="Update interval seconds (3–30)",
            default=str(settings.update_interval_seconds),
            required=True,
            max_length=2,
        )

        self.add_item(self.max_timers)
        self.add_item(self.min_minutes)
        self.add_item(self.max_minutes)
        self.add_item(self.update_interval)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            updated = self.cog._apply_guild_settings(
                self.guild_id,
                max_concurrent_timers=int(self.max_timers.value),
                min_duration_minutes=int(self.min_minutes.value),
                max_duration_minutes=int(self.max_minutes.value),
                update_interval_seconds=int(self.update_interval.value),
            )
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        except Exception:
            await interaction.response.send_message("❌ Failed to update settings.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Timer Settings Updated",
            description=(
                f"Max timers: **{updated.max_concurrent_timers}**\n"
                f"Duration range: **{updated.min_duration_minutes}–{updated.max_duration_minutes}** minutes\n"
                f"Update interval: **{updated.update_interval_seconds}** seconds"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="These settings apply immediately in this server")
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
