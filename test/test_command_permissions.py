"""Every command and button that changes league data must be guarded, checked by loading the real bot."""

import importlib
import os
from unittest.mock import Mock

import httpx
import pytest
from discord import app_commands

os.environ.setdefault('DISCORD_TOKEN', 'test-token')

import bot as bot_module  # noqa: E402
from db.supabase_client import SupabaseClient  # noqa: E402
from utils import config_manager  # noqa: E402

# Checked with bot.is_owner inside the command: only the bot owner, not server admins.
OWNER_ONLY = {"admin-cogs"}


@pytest.fixture
async def loaded_bot(monkeypatch):
    """The real bot with every cog loaded, no network, and global state restored afterwards."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_secret_test")

    def no_network(self, request, **kwargs):
        raise httpx.ConnectError("network is disabled in tests")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    previous_manager = config_manager._config_manager
    SupabaseClient.reset()
    config_manager.init_config_manager(Mock())

    bot = bot_module.TouchlineBot()
    for extension in bot.discover_cogs():
        # Not bot.load_extension: it swaps fresh module objects into sys.modules, which breaks other tests
        # that patch these modules by name.
        await importlib.import_module(extension).setup(bot)
    yield bot

    SupabaseClient.reset()
    config_manager._config_manager = previous_manager


def _is_admin_check(check) -> bool:
    return getattr(check, "__qualname__", "").startswith("admin_only.")


def _admin_commands(bot):
    return [
        command for command in bot.tree.walk_commands()
        if isinstance(command, app_commands.Command) and (command.description or "").startswith("[ADMIN]")
    ]


@pytest.mark.asyncio
async def test_every_admin_command_carries_the_admin_check(loaded_bot):
    admin_commands = _admin_commands(loaded_bot)
    assert len(admin_commands) >= 9, "the walk should find the admin commands"

    unguarded = [
        command.qualified_name for command in admin_commands
        if command.qualified_name not in OWNER_ONLY and not any(_is_admin_check(c) for c in command.checks)
    ]
    assert unguarded == []

