"""
Centralized error handling for NBLBot commands.
Provides standard error messages with contextual suggestions.
"""

import discord
import logging
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Standard error categories for consistent messaging."""
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INPUT = "invalid_input"
    CONFLICT = "conflict"
    NO_ACTIVE_SEASON = "no_active_season"
    ROSTER_FULL = "roster_full"
    ALREADY_ON_TEAM = "already_on_team"
    NOT_TEAM_OWNER = "not_team_owner"
    VALIDATION_ERROR = "validation_error"
    DATABASE_ERROR = "database_error"
    DISCORD_ERROR = "discord_error"


class StandardError:
    """Standard error message builder with contextual suggestions."""

    @staticmethod
    def player_not_found(player_name: str, suggestion: Optional[str] = None) -> tuple[str, str]:
        """Player not found in database or cache."""
        title = "❌ Player Not Found"
        message = f"Could not find player **{player_name}**."
        
        if suggestion:
            message += f"\n\n💡 {suggestion}"
        else:
            message += "\n\n💡 Try using autocomplete when typing player names—it'll show available players for your team."
        
        return title, message

    @staticmethod
    def team_not_found(team_code: str, suggestion: Optional[str] = None) -> tuple[str, str]:
        """Team not found in database or cache."""
        title = "❌ Team Not Found"
        message = f"Could not find team **{team_code}**."
        
        if suggestion:
            message += f"\n\n💡 {suggestion}"
        else:
            message += "\n\n💡 Try using autocomplete to see all available teams."
        
        return title, message

    @staticmethod
    def no_team_owner() -> tuple[str, str]:
        """User doesn't own a team."""
        title = "❌ No Team Found"
        message = "You don't own a team in this league. Contact an admin to set up your team."
        return title, message

    @staticmethod
    def no_active_season() -> tuple[str, str]:
        """No active season configured."""
        title = "⚠️ No Active Season"
        message = "There's no active season configured for this league. An admin needs to set one up."
        return title, message

    @staticmethod
    def no_permission(required: str) -> tuple[str, str]:
        """User lacks required permissions."""
        title = "❌ Permission Denied"
        message = f"You need **{required}** permissions to use this command. Contact a server admin."
        return title, message

    @staticmethod
    def invalid_input(field: str, reason: str, suggestion: Optional[str] = None) -> tuple[str, str]:
        """Invalid input provided."""
        title = "❌ Invalid Input"
        message = f"**{field}**: {reason}"
        
        if suggestion:
            message += f"\n\n💡 {suggestion}"
        
        return title, message

    @staticmethod
    def already_on_team(player_name: str, team_name: str) -> tuple[str, str]:
        """Player already on team."""
        title = "❌ Player Already Signed"
        message = f"**{player_name}** is already on **{team_name}**."
        return title, message

    @staticmethod
    def not_on_team(player_name: str) -> tuple[str, str]:
        """Player not on the specified team."""
        title = "❌ Player Not On Team"
        message = f"**{player_name}** is not on this team, so they cannot be released."
        return title, message

    @staticmethod
    def roster_full(team_name: str, max_size: int) -> tuple[str, str]:
        """Team roster is full."""
        title = "❌ Roster Full"
        message = f"**{team_name}**'s roster is full ({max_size} players max). Release someone first."
        return title, message

    @staticmethod
    def trade_conflict(reason: str) -> tuple[str, str]:
        """Trade validation failed."""
        title = "❌ Trade Not Allowed"
        message = f"This trade cannot be completed: {reason}"
        return title, message

    @staticmethod
    def signing_expired(player_name: str) -> tuple[str, str]:
        """Signing offer has expired."""
        title = "⏰ Offer Expired"
        message = f"The signing offer for **{player_name}** has expired. You'll need to send a new offer."
        return title, message

    @staticmethod
    def no_data_available(context: str = "data") -> tuple[str, str]:
        """No data available for the query."""
        title = "📊 No Data Available"
        message = f"No {context} found for your query. Try different filters or check the active season."
        return title, message

    @staticmethod
    def database_error(operation: str = "database operation") -> tuple[str, str]:
        """Database error occurred."""
        title = "⚠️ Database Error"
        message = f"An error occurred while performing the {operation}. Please try again in a moment."
        logger.error(f"Database error during {operation}")
        return title, message

    @staticmethod
    def unexpected_error() -> tuple[str, str]:
        """Unexpected error occurred."""
        title = "❌ Unexpected Error"
        message = "Something went wrong. The bot admins have been notified. Please try again later."
        logger.error("Unexpected error in command execution")
        return title, message

    @staticmethod
    def command_timed_out() -> tuple[str, str]:
        """Command execution timed out."""
        title = "⏱️ Command Timeout"
        message = "This command took too long to complete. Please try again."
        return title, message

    @staticmethod
    def insufficient_funds(player_name: str, cap_space: int, required: int) -> tuple[str, str]:
        """Team doesn't have enough salary cap space."""
        title = "💰 Insufficient Salary Cap"
        message = f"Your team only has **${cap_space}** cap space, but **{player_name}** costs **${required}**."
        return title, message

    @staticmethod
    def invalid_season_combination(player: str, team: str, season: str) -> tuple[str, str]:
        """Player, team, and season don't have valid combination."""
        title = "❌ Invalid Combination"
        message = f"**{player}** is not on **{team}** in **{season}**, so this operation isn't valid."
        return title, message

    @staticmethod
    def user_not_registered() -> tuple[str, str]:
        """User is not registered in the system."""
        title = "❌ Not Registered"
        message = "You're not registered in the league system. Contact an admin to register you."
        return title, message

    @staticmethod
    def same_team_trade() -> tuple[str, str]:
        """Cannot trade with yourself."""
        title = "❌ Invalid Trade"
        message = "You can't trade with your own team. Trade with a different team instead."
        return title, message

    @staticmethod
    def success(title: str, message: str) -> tuple[str, str]:
        """Success message."""
        return f"✅ {title}", message


async def send_error(
    interaction: discord.Interaction,
    category: ErrorCategory,
    *,
    ephemeral: bool = True,
    **kwargs
) -> None:
    """
    Send a standardized error message to a user.
    
    Args:
        interaction: Discord interaction
        category: Error category
        ephemeral: Whether to send as ephemeral (default True)
        **kwargs: Additional context for the error message
    """
    # Map category to handler
    handlers = {
        ErrorCategory.NOT_FOUND: lambda: StandardError.player_not_found(
            kwargs.get("name", "unknown"), kwargs.get("suggestion")
        ),
        ErrorCategory.NO_ACTIVE_SEASON: lambda: StandardError.no_active_season(),
        ErrorCategory.PERMISSION_DENIED: lambda: StandardError.no_permission(
            kwargs.get("required", "administrator")
        ),
        ErrorCategory.INVALID_INPUT: lambda: StandardError.invalid_input(
            kwargs.get("field", "Input"), kwargs.get("reason", "Invalid"), kwargs.get("suggestion")
        ),
        ErrorCategory.CONFLICT: lambda: StandardError.already_on_team(
            kwargs.get("player", "Player"), kwargs.get("team", "Team")
        ),
        ErrorCategory.ROSTER_FULL: lambda: StandardError.roster_full(
            kwargs.get("team", "Team"), kwargs.get("max_size", 12)
        ),
        ErrorCategory.NOT_TEAM_OWNER: lambda: StandardError.no_team_owner(),
        ErrorCategory.DATABASE_ERROR: lambda: StandardError.database_error(
            kwargs.get("operation", "database operation")
        ),
        ErrorCategory.DISCORD_ERROR: lambda: StandardError.unexpected_error(),
        ErrorCategory.VALIDATION_ERROR: lambda: StandardError.invalid_input(
            kwargs.get("field", "Input"), kwargs.get("reason", "Invalid")
        ),
    }

    title, message = handlers.get(category, StandardError.unexpected_error)()

    embed = discord.Embed(title=title, description=message, color=discord.Color.red())
    
    # Defer if not already deferred
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)


async def send_success(
    interaction: discord.Interaction,
    title: str,
    message: str,
    *,
    ephemeral: bool = False,
    color: discord.Color = discord.Color.green(),
) -> None:
    """
    Send a standardized success message to a user.
    
    Args:
        interaction: Discord interaction
        title: Success title
        message: Success message
        ephemeral: Whether to send as ephemeral
        color: Embed color (default green)
    """
    embed = discord.Embed(
        title=f"✅ {title}",
        description=message,
        color=color
    )
    
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)


async def send_info(
    interaction: discord.Interaction,
    title: str,
    message: str,
    *,
    ephemeral: bool = False,
) -> None:
    """
    Send an informational message to a user.
    
    Args:
        interaction: Discord interaction
        title: Info title
        message: Info message
        ephemeral: Whether to send as ephemeral
    """
    embed = discord.Embed(
        title=f"ℹ️ {title}",
        description=message,
        color=discord.Color.blue()
    )
    
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)


class ConfirmationModal(discord.ui.Modal):
    """Modal for confirming risky operations."""
    
    def __init__(self, title: str, operation_description: str, callback):
        super().__init__(title=title)
        self.operation_description = operation_description
        self.callback = callback
        
        # Add confirmation input
        self.confirmation = discord.ui.TextInput(
            label="Confirm Operation",
            placeholder=f"Type 'confirm' to proceed with: {operation_description[:50]}",
            min_length=7,
            max_length=7
        )
        self.add_item(self.confirmation)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        if self.confirmation.value.lower() != "confirm":
            await interaction.response.send_message(
                "❌ Operation cancelled. You must type 'confirm' to proceed.",
                ephemeral=True
            )
            return
        
        await self.callback(interaction)


class ConfirmationView(discord.ui.View):
    """View for confirming risky operations with yes/no buttons."""
    
    def __init__(self, operation_description: str, callback, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.operation_description = operation_description
        self.callback = callback
        self.result = None
    
    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger, emoji="✔️")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm button."""
        await interaction.response.defer(ephemeral=True)
        self.result = True
        await self.callback(interaction)
        self.stop()
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel button."""
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("Operation cancelled.", ephemeral=True)
        self.result = False
        self.stop()
    
    async def on_timeout(self):
        """Handle timeout."""
        logger.info(f"Confirmation view timed out for: {self.operation_description}")
