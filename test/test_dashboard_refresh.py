"""Dashboard changes reload only what they changed."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.dashboard import core
from cogs.dashboard.settings import SettingsPage


class Page(core.DashboardPage):
    async def render(self):
        return Mock()


def _page():
    page = Page(Mock(user_id=1, guild_id=99, bot=Mock()))
    page.show = AsyncMock()
    return page


def _interaction():
    interaction = Mock()
    interaction.response.is_done = Mock(return_value=True)
    return interaction


@pytest.fixture
def refresh():
    with patch.object(core, "refresh_caches", AsyncMock()) as mocked, patch.object(core.board_events, "league_changed"):
        yield mocked


@pytest.mark.asyncio
async def test_league_changes_reload_the_caches_by_default(refresh):
    await _page().apply(_interaction(), AsyncMock(), "Saved.")

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_change_can_opt_out_of_the_reload(refresh):
    await _page().apply(_interaction(), AsyncMock(), "Saved.", refresh=False)

    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_change_reloads_nothing(refresh):
    await _page().apply(_interaction(), AsyncMock(side_effect=RuntimeError("boom")), "Saved.")

    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_saving_server_settings_does_not_reload_league_data(refresh):
    page = SettingsPage(Mock(user_id=1, guild_id=99, bot=Mock()))
    page.config = {"admin_role_id": "5"}
    page.show = AsyncMock()

    with patch("cogs.dashboard.settings.ConfigUtility.save_guild_config", AsyncMock()) as save:
        await page._save(_interaction(), "Saved.")

    save.assert_awaited_once()
    refresh.assert_not_awaited()
