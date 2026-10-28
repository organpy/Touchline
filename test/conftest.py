"""Pytest configuration and shared fixtures."""

import pytest
import asyncio

pytest_plugins = ('pytest_asyncio',)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )



@pytest.fixture
async def loaded_bot(monkeypatch):
    """The real bot with every cog loaded, no network, and global state restored afterwards."""
    import importlib
    import os
    from unittest.mock import Mock

    import httpx

    os.environ.setdefault('DISCORD_TOKEN', 'test-token')
    import bot as bot_module
    from db.supabase_client import SupabaseClient
    from utils import config_manager

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
