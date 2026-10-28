"""Only server administrators can change the admin role."""

from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.dashboard.settings import SettingsPage


def _page():
    page = SettingsPage(Mock(user_id=1, guild_id=99))
    page.config = {"admin_role_id": "555"}
    page.show = AsyncMock()
    page._save = AsyncMock()
    return page


def _interaction(*, administrator):
    interaction = Mock()
    interaction.user = Mock(spec=discord.Member)
    interaction.user.guild_permissions = discord.Permissions(administrator=administrator)
    return interaction


def _select(role_id):
    role = Mock(id=role_id, mention=f"<@&{role_id}>")
    return Mock(values=[role])


@pytest.mark.asyncio
async def test_admin_role_holder_cannot_change_the_admin_role():
    page = _page()

    await page._set_role(_interaction(administrator=False), _select(777))

    assert page.config["admin_role_id"] == "555"
    page._save.assert_not_awaited()
    assert "Only server administrators" in page.notice
    page.show.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_administrator_can_change_the_admin_role():
    page = _page()

    await page._set_role(_interaction(administrator=True), _select(777))

    assert page.config["admin_role_id"] == "777"
    page._save.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_administrator_can_clear_the_admin_role():
    page = _page()

    await page._set_role(_interaction(administrator=True), Mock(values=[]))

    assert page.config["admin_role_id"] == ""
    page._save.assert_awaited_once()
