"""
Tests for quick-win optimizations: error handler, search, help system, dashboard metrics.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch
import discord
from discord.ext import commands

# Import modules to test
from utils.error_handler import StandardError, ErrorCategory, ConfirmationView
from cogs.search import fuzzy_match, search_players, search_teams
from cogs.help.help import CommandCategory, HelpView


class TestStandardError:
    """Test error handler standard messages."""
    
    def test_player_not_found(self):
        """Test player not found error."""
        title, message = StandardError.player_not_found("Ace")
        assert "❌" in title
        assert "Ace" in message
        assert "autocomplete" in message.lower()
    
    def test_team_not_found(self):
        """Test team not found error."""
        title, message = StandardError.team_not_found("PANDAS")
        assert "❌" in title
        assert "PANDAS" in message
    
    def test_no_team_owner(self):
        """Test no team owner error."""
        title, message = StandardError.no_team_owner()
        assert "❌" in title
        assert "team" in message.lower()
    
    def test_no_active_season(self):
        """Test no active season error."""
        title, message = StandardError.no_active_season()
        assert "⚠️" in title
        assert "season" in message.lower()
    
    def test_already_on_team(self):
        """Test already on team error."""
        title, message = StandardError.already_on_team("Ace", "Pandas")
        assert "Already Signed" in title
        assert "Ace" in message
        assert "Pandas" in message
    
    def test_roster_full(self):
        """Test roster full error."""
        title, message = StandardError.roster_full("Pandas", 12)
        assert "Roster Full" in title
        assert "12" in message


class TestFuzzyMatch:
    """Test fuzzy matching for search."""
    
    def test_exact_match(self):
        """Test exact match gets perfect score."""
        score = fuzzy_match("Ace", "Ace")
        assert score == 1.0
    
    def test_prefix_match(self):
        """Test prefix match gets high score."""
        score = fuzzy_match("Ac", "Ace")
        assert score > 0.8
    
    def test_substring_match(self):
        """Test substring match gets medium score."""
        score = fuzzy_match("ce", "Ace")
        assert score > 0.6
    
    def test_no_match(self):
        """Test no match gets zero score."""
        score = fuzzy_match("xyz", "Ace")
        assert score < 0.6
    
    def test_case_insensitive(self):
        """Test case insensitive matching."""
        score1 = fuzzy_match("ace", "Ace")
        score2 = fuzzy_match("ACE", "ace")
        assert score1 > 0.8
        assert score2 > 0.8


class TestCommandCategory:
    """Test command categories."""
    
    def test_category_enum_exists(self):
        """Test that all expected categories exist."""
        categories = [
            CommandCategory.STATS,
            CommandCategory.TEAM_OWNER,
            CommandCategory.MEMBERS,
            CommandCategory.SEARCH,
            CommandCategory.SCORE,
            CommandCategory.ADMIN,
            CommandCategory.HELP,
            CommandCategory.OTHER,
        ]
        assert len(categories) == 8
    
    def test_category_values(self):
        """Test category display values."""
        emoji, description = CommandCategory.STATS.value
        assert "📊" in emoji
        assert len(description) > 0
        assert len(emoji) <= 10  # Emoji is short


class TestConfirmationView:
    """Test confirmation dialog."""
    
    @pytest.mark.asyncio
    async def test_confirmation_view_creation(self):
        """Test that confirmation view can be created."""
        async def dummy_callback(interaction):
            pass
        
        view = ConfirmationView("Test operation", dummy_callback)
        assert view is not None
        assert len(view.children) == 2  # Confirm and cancel buttons
    
    @pytest.mark.asyncio
    async def test_confirmation_buttons_exist(self):
        """Test confirmation and cancel buttons exist."""
        async def dummy_callback(interaction):
            pass
        
        view = ConfirmationView("Test", dummy_callback)
        buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
        assert len(buttons) >= 2


# Integration tests with mocked Discord
@pytest.mark.asyncio
async def test_help_view_integration():
    """Test help view can be created with command categories."""
    bot = MagicMock(spec=commands.Bot)
    
    # Mock walk_commands to return sample commands
    mock_commands = [
        MagicMock(name="average", description="[STATS] Show averages"),
        MagicMock(name="sign", description="[TEAM OWNER] Sign a player"),
        MagicMock(name="search", description="[SEARCH] Find players"),
    ]
    bot.tree.walk_commands.return_value = mock_commands
    
    # Create categories dict
    categories = {
        CommandCategory.STATS: [("average", "[STATS] Show averages")],
        CommandCategory.TEAM_OWNER: [("sign", "[TEAM OWNER] Sign a player")],
        CommandCategory.SEARCH: [("search", "[SEARCH] Find players")],
    }
    
    view = HelpView(bot, categories)
    assert view is not None
    assert view.bot == bot


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
