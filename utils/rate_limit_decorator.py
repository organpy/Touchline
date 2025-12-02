"""
Rate limiting decorator for Discord commands.
Automatically applies rate limits to prevent command abuse.
"""
import discord
from discord import app_commands
from functools import wraps
import logging
from typing import Optional
from utils.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


def rate_limit(
    max_requests: int = 5,
    window_seconds: int = 60,
    per_user: bool = True,
    per_guild: bool = False,
    message: Optional[str] = None
):
    """
    Rate limit decorator for Discord commands.
    
    Args:
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds
        per_user: Apply rate limit per user
        per_guild: Apply rate limit per guild
        message: Custom rate limit message
    
    Example:
        @app_commands.command()
        @rate_limit(max_requests=3, window_seconds=60)
        async def my_command(interaction: discord.Interaction):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            limiter = get_rate_limiter()
            
            # Check user rate limit
            if per_user:
                allowed, retry_after = limiter.check_rate_limit(
                    interaction.user.id,
                    interaction.guild_id if per_guild else None
                )
                
                if not allowed:
                    retry_msg = message or (
                        f"⏱️ Rate limit exceeded. "
                        f"Try again in {int(retry_after or 0)} seconds."
                    )
                    await interaction.response.send_message(
                        retry_msg,
                        ephemeral=True
                    )
                    logger.warning(
                        f"Rate limit hit: user={interaction.user.id}, "
                        f"command={func.__name__}, retry_after={retry_after:.1f}s"
                    )
                    return
            
            # Call original function
            return await func(self, interaction, *args, **kwargs)
        
        return wrapper
    return decorator


def admin_bypass_rate_limit(func):
    """
    Decorator that bypasses rate limits for bot owners and admins.
    Use this BEFORE @rate_limit decorator.
    
    Example:
        @app_commands.command()
        @admin_bypass_rate_limit
        @rate_limit(max_requests=3, window_seconds=60)
        async def my_command(interaction: discord.Interaction):
            ...
    """
    @wraps(func)
    async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
        # Check if user is bot owner
        bot = interaction.client
        if hasattr(bot, 'owner_id') and interaction.user.id == bot.owner_id:
            return await func(self, interaction, *args, **kwargs)
        
        # Check if user has administrator permission
        if interaction.guild and interaction.user.guild_permissions.administrator:
            return await func(self, interaction, *args, **kwargs)
        
        # Otherwise, proceed with normal flow (rate limit will apply)
        return await func(self, interaction, *args, **kwargs)
    
    return wrapper
