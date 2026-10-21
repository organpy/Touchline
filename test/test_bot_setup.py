"""Tests for bot-level configuration in bot.py."""

import os
import sys
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault('DISCORD_TOKEN', 'test-token')

import bot as bot_module  # noqa: E402


@pytest.fixture
def nbl_bot():
    with patch.object(bot_module, 'get_supabase', return_value=Mock()):
        yield bot_module.TouchlineBot()


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


def test_only_needed_privileged_intents(nbl_bot):
    assert nbl_bot.intents.members is True
    assert nbl_bot.intents.message_content is False


@pytest.mark.skipif(sys.platform == "win32", reason="asyncio signal handlers are Unix-only")
@pytest.mark.asyncio
async def test_sigterm_closes_the_bot():
    import asyncio
    import signal
    from unittest.mock import AsyncMock

    fake_bot = Mock(close=AsyncMock())
    loop = asyncio.get_running_loop()
    bot_module.install_shutdown_handlers(fake_bot)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(10):
            await asyncio.sleep(0.01)
            if fake_bot.close.await_count:
                break
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)

    fake_bot.close.assert_awaited_once()


def _add_command(bot, name):
    async def callback(interaction):
        pass

    bot.tree.command(name=name, description=f"{name} command")(callback)


@pytest.mark.asyncio
async def test_commands_sync_only_when_they_change(nbl_bot, tmp_path):
    from unittest.mock import AsyncMock

    state_file = str(tmp_path / "sync_hash")
    nbl_bot.tree.sync = AsyncMock(return_value=[])
    _add_command(nbl_bot, "standings")

    await nbl_bot.sync_commands_if_changed(state_file)
    await nbl_bot.sync_commands_if_changed(state_file)
    assert nbl_bot.tree.sync.await_count == 1

    _add_command(nbl_bot, "schedule")
    await nbl_bot.sync_commands_if_changed(state_file)
    assert nbl_bot.tree.sync.await_count == 2


@pytest.mark.asyncio
async def test_commands_resync_for_a_different_application(nbl_bot, tmp_path):
    from unittest.mock import AsyncMock

    state_file = str(tmp_path / "sync_hash")
    nbl_bot.tree.sync = AsyncMock(return_value=[])
    await nbl_bot.sync_commands_if_changed(state_file)

    nbl_bot._connection.application_id = 1234
    await nbl_bot.sync_commands_if_changed(state_file)

    assert nbl_bot.tree.sync.await_count == 2


@pytest.mark.asyncio
async def test_failed_command_sync_is_retried_next_boot(nbl_bot, tmp_path):
    from unittest.mock import AsyncMock

    state_file = tmp_path / "sync_hash"
    nbl_bot.tree.sync = AsyncMock(side_effect=RuntimeError("discord down"))

    await nbl_bot.sync_commands_if_changed(str(state_file))

    assert not state_file.exists()


def test_bot_pings_nobody_by_default(nbl_bot):
    allowed = nbl_bot.allowed_mentions
    assert allowed.everyone is False
    assert allowed.roles is False
    assert allowed.users is False
    assert allowed.replied_user is False
