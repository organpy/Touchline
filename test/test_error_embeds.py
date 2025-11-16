"""
Tests for error embed utilities.
"""

import pytest
import discord

from utils.error_embeds import (
    error_embed,
    not_found_embed,
    permission_error_embed,
    database_error_embed,
    timeout_error_embed,
    rate_limit_embed,
    validation_error_embed,
)


class TestErrorEmbeds:
    """Test standardized error embed creation."""

    def test_error_embed_basic(self):
        """Test basic error embed creation."""
        embed = error_embed(
            title="Test Error",
            description="This is a test error",
        )
        
        assert embed.title == "Test Error"
        assert embed.description == "This is a test error"
        assert embed.color == discord.Color.red()

    def test_error_embed_with_hint(self):
        """Test error embed with hint."""
        embed = error_embed(
            title="Missing Item",
            description="Could not find item",
            hint="Try again with a different name",
        )
        
        assert embed.title == "Missing Item"
        assert len(embed.fields) == 1
        assert embed.fields[0].name == "💡 Hint"
        assert embed.fields[0].value == "Try again with a different name"

    def test_error_embed_custom_color(self):
        """Test error embed with custom color."""
        embed = error_embed(
            title="Warning",
            description="This is a warning",
            color=discord.Color.orange(),
        )
        
        assert embed.color == discord.Color.orange()

    def test_not_found_embed(self):
        """Test not found embed."""
        embed = not_found_embed("Player", "John")
        
        assert "Player Not Found" in embed.title
        assert "John" in embed.description
        assert len(embed.fields) > 0

    def test_permission_error_embed(self):
        """Test permission error embed."""
        embed = permission_error_embed()
        
        assert "Permission" in embed.title
        assert embed.color == discord.Color.red()

    def test_database_error_embed(self):
        """Test database error embed."""
        embed = database_error_embed()
        
        assert "Database" in embed.title
        assert "unable" in embed.description.lower() or "connect" in embed.description.lower()

    def test_timeout_error_embed(self):
        """Test timeout error embed."""
        embed = timeout_error_embed()
        
        assert "Timeout" in embed.title or "⏱️" in embed.title
        assert embed.color == discord.Color.red()

    def test_rate_limit_embed(self):
        """Test rate limit embed with retry time."""
        embed = rate_limit_embed(retry_after_seconds=5)
        
        assert "Rate" in embed.title or "⏸️" in embed.title
        assert "5" in embed.description
        assert embed.color == discord.Color.gold()

    def test_validation_error_embed(self):
        """Test validation error with field name."""
        embed = validation_error_embed(
            field_name="Team Code",
            issue="Must be uppercase and 3-10 characters",
        )
        
        assert "Invalid" in embed.title or "⚠️" in embed.title
        assert "Team Code" in embed.description
        assert "uppercase" in embed.description

    def test_embed_structure(self):
        """Test that all embeds have proper structure."""
        embeds = [
            permission_error_embed(),
            database_error_embed(),
            timeout_error_embed(),
            rate_limit_embed(5),
        ]
        
        for embed in embeds:
            assert embed.title is not None
            assert embed.description is not None
            assert embed.color is not None
            # Should be valid Discord embed
            assert len(embed.title) > 0
            assert len(embed.description) > 0


class TestEmbedHierarchy:
    """Test embed creation and consistency."""

    def test_all_error_embeds_are_embed_type(self):
        """Test that all functions return discord.Embed objects."""
        embeds = [
            error_embed("Title", "Desc"),
            not_found_embed("Item", "test"),
            permission_error_embed(),
            database_error_embed(),
            timeout_error_embed(),
            rate_limit_embed(5),
            validation_error_embed("Field", "Issue"),
        ]
        
        for embed in embeds:
            assert isinstance(embed, discord.Embed)

    def test_embed_field_presence(self):
        """Test that hint embeds have fields."""
        # With hint
        with_hint = error_embed("Test", "Desc", hint="Try this")
        assert len(with_hint.fields) >= 1
        
        # Without hint
        no_hint = error_embed("Test", "Desc")
        assert len(no_hint.fields) == 0

    def test_rate_limit_retry_formatting(self):
        """Test various retry-after times."""
        for seconds in [1, 5, 30, 60, 300]:
            embed = rate_limit_embed(seconds)
            assert str(seconds) in embed.description
