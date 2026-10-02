"""Tests for bot-level configuration in bot.py."""

import os
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault('DISCORD_TOKEN', 'test-token')

import bot as bot_module  # noqa: E402


@pytest.fixture
def nbl_bot():
    with patch.object(bot_module, 'get_supabase', return_value=Mock()):
        yield bot_module.NBLBot()


def test_commands_are_server_only(nbl_bot):
    contexts = nbl_bot.tree.allowed_contexts
    assert contexts.guild is True
    assert contexts.dm_channel is False
    assert contexts.private_channel is False

    installs = nbl_bot.tree.allowed_installs
    assert installs.guild is True
    assert installs.user is False


def _failed_interaction():
    from unittest.mock import AsyncMock
    interaction = Mock()
    interaction.command = Mock(qualified_name="standings")
    interaction.user = Mock(id=1)
    interaction.guild_id = 2
    interaction.response = Mock()
    interaction.response.is_done = Mock(return_value=True)
    interaction.followup = Mock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_tree_error_handler_is_installed(nbl_bot):
    assert nbl_bot.tree.on_error == nbl_bot.on_app_command_error


@pytest.mark.asyncio
async def test_unexpected_error_is_logged_and_user_is_told(nbl_bot, caplog):
    from discord import app_commands
    interaction = _failed_interaction()
    original = RuntimeError("boom")
    error = app_commands.CommandInvokeError(Mock(qualified_name="standings"), original)

    await nbl_bot.on_app_command_error(interaction, error)

    embed = interaction.followup.send.await_args.kwargs['embed']
    assert "Something went wrong" in embed.title
    assert interaction.followup.send.await_args.kwargs['ephemeral'] is True
    assert any(record.exc_info and record.exc_info[1] is original for record in caplog.records)


@pytest.mark.asyncio
async def test_permission_failure_gets_permission_embed(nbl_bot):
    from discord import app_commands
    interaction = _failed_interaction()

    await nbl_bot.on_app_command_error(interaction, app_commands.MissingPermissions(['administrator']))

    assert "Permission Denied" in interaction.followup.send.await_args.kwargs['embed'].title
