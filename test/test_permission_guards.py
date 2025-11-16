"""
Tests for permission guard decorators.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import discord

from utils.permission_guards import require_owner_or_admin, require_team_owner


class TestPermissionGuards:
    """Test permission checking decorators."""

    @pytest.mark.asyncio
    async def test_require_owner_or_admin_with_bot_owner(self):
        """Test that bot owner can execute admin command."""
        mock_func = AsyncMock()
        decorated = require_owner_or_admin(mock_func)
        
        # Mock cog with bot reference
        mock_cog = MagicMock()
        mock_cog.bot = MagicMock()
        mock_cog.bot.owner_id = 12345
        
        # Mock interaction as bot owner
        interaction = MagicMock()
        interaction.user.id = 12345
        
        await decorated(mock_cog, interaction)
        
        # Function should be called
        mock_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_require_owner_or_admin_with_guild_admin(self):
        """Test that guild admin can execute admin command."""
        mock_func = AsyncMock()
        decorated = require_owner_or_admin(mock_func)
        
        mock_cog = MagicMock()
        mock_cog.bot = MagicMock()
        mock_cog.bot.owner_id = 99999  # Different from user
        
        interaction = MagicMock()
        interaction.user.id = 12345
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.response = AsyncMock()
        
        await decorated(mock_cog, interaction)
        
        # Function should be called
        mock_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_require_owner_or_admin_denies_regular_user(self):
        """Test that regular user cannot execute admin command."""
        mock_func = AsyncMock()
        decorated = require_owner_or_admin(mock_func)
        
        mock_cog = MagicMock()
        mock_cog.bot = MagicMock()
        mock_cog.bot.owner_id = 99999
        
        interaction = MagicMock()
        interaction.user.id = 12345
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        
        await decorated(mock_cog, interaction)
        
        # Function should NOT be called
        mock_func.assert_not_called()
        
        # But error response should be sent
        interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_require_team_owner_defers_response(self):
        """Test that require_team_owner defers interaction."""
        mock_func = AsyncMock()
        decorated = require_team_owner(mock_func)
        
        mock_cog = MagicMock()
        
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        
        await decorated(mock_cog, interaction)
        
        # Should defer before calling function
        interaction.response.defer.assert_called_once()
        mock_func.assert_called_once()


class TestPermissionGuardIntegration:
    """Integration tests for permission guards."""

    @pytest.mark.asyncio
    async def test_owner_or_admin_with_multiple_users(self):
        """Test with multiple user scenarios."""
        mock_func = AsyncMock()
        decorated = require_owner_or_admin(mock_func)
        
        mock_cog = MagicMock()
        mock_cog.bot = MagicMock()
        mock_cog.bot.owner_id = 1
        
        # Scenario 1: Owner
        interaction = MagicMock()
        interaction.user.id = 1
        interaction.response = AsyncMock()
        
        await decorated(mock_cog, interaction)
        assert mock_func.call_count == 1
        
        # Scenario 2: Admin
        interaction = MagicMock()
        interaction.user.id = 2
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.response = AsyncMock()
        
        await decorated(mock_cog, interaction)
        assert mock_func.call_count == 2
        
        # Scenario 3: Regular user
        interaction = MagicMock()
        interaction.user.id = 3
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.response = AsyncMock()
        
        await decorated(mock_cog, interaction)
        
        # Should still be 2 (not called for regular user)
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_name(self):
        """Test that decorator preserves original function metadata."""
        async def my_command(self, interaction):
            """My command documentation."""
            pass
        
        decorated = require_owner_or_admin(my_command)
        
        # functools.wraps should preserve __name__ and __doc__
        assert decorated.__name__ == "my_command"
        assert "My command" in decorated.__doc__
