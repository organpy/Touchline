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
