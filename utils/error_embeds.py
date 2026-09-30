"""
Standardized error responses and embeds for consistent user feedback.
"""

import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)


def error_embed(
    title: str,
    description: str,
    hint: Optional[str] = None,
    color: discord.Color = discord.Color.red(),
) -> discord.Embed:
    """
    Create a standardized error embed.

    Args:
        title: Error title (e.g., "❌ Player Not Found")
        description: Main error message
        hint: Optional hint or suggestion for fixing the issue
        color: Embed color (defaults to red)

    Returns:
        Formatted error embed ready to send
    """
    embed = discord.Embed(title=title, description=description, color=color)
    if hint:
        embed.add_field(name="💡 Hint", value=hint, inline=False)
    return embed


def not_found_embed(entity: str, identifier: str) -> discord.Embed:
    """
    Create embed for "not found" error.

    Args:
        entity: What wasn't found (e.g., "Player", "Team", "Season")
        identifier: What was being searched for

    Returns:
        Error embed
    """
    return error_embed(
        title=f"❌ {entity} Not Found",
        description=f"Could not find {entity.lower()}: `{identifier}`",
        hint=f"Check the spelling and try again, or use `/help` to see available {entity.lower()}s.",
    )


def permission_error_embed() -> discord.Embed:
    """Create embed for permission denied errors."""
    return error_embed(
        title="❌ Permission Denied",
        description="You don't have permission to run this command.",
        hint="This command may require admin status, guild permissions, or team ownership.",
    )


def database_error_embed() -> discord.Embed:
    """Create embed for database/connection errors."""
    return error_embed(
        title="❌ Database Error",
        description="Unable to connect to the database or retrieve data.",
        hint="This is a temporary issue. Please try again in a moment.",
    )


def timeout_error_embed() -> discord.Embed:
    """Create embed for timeout errors."""
    return error_embed(
        title="⏱️ Request Timeout",
        description="Your request took too long to process.",
        hint="Try again with fewer results or a simpler query.",
    )


def rate_limit_embed(retry_after_seconds: int) -> discord.Embed:
    """
    Create embed for rate limit errors.

    Args:
        retry_after_seconds: Seconds to wait before retrying

    Returns:
        Error embed
    """
    return error_embed(
        title="⏸️ Rate Limited",
        description=f"Please wait {retry_after_seconds} seconds before trying again.",
        hint="The bot is processing many requests. Your request will be ready soon.",
        color=discord.Color.gold(),
    )


def validation_error_embed(field_name: str, issue: str) -> discord.Embed:
    """
    Create embed for validation errors.

    Args:
        field_name: Name of field that failed validation
        issue: Description of what's wrong

    Returns:
        Error embed
    """
    return error_embed(
        title="⚠️ Invalid Input",
        description=f"**{field_name}**: {issue}",
        hint="Please review the command options and try again.",
        color=discord.Color.orange(),
    )


async def send_error(
    interaction: discord.Interaction, embed: discord.Embed, log: bool = True
) -> None:
    """
    Send error embed to user, handling defer state.

    Args:
        interaction: Discord interaction
        embed: Error embed to send
        log: Whether to log the error (default True)
    """
    try:
        if log:
            logger.warning(f"Error in command {interaction.command.name}: {embed.title}")

        if interaction.response.is_done():
            # Already responded, use followup
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # First response
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to send error embed: {e}", exc_info=True)
