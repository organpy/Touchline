"""Tests for /admin-cogs access control."""

from unittest.mock import AsyncMock, Mock

import pytest

from cogs.admin.cog_management import CogManagementCog


def _interaction(*, administrator: bool):
    interaction = Mock()
    interaction.user = Mock(id=1)
    interaction.user.guild_permissions = Mock(administrator=administrator)
    interaction.response = Mock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = Mock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_server_admin_who_is_not_owner_is_refused():
    bot = Mock(is_owner=AsyncMock(return_value=False), extensions={})
    bot.unload_extension = AsyncMock()
    interaction = _interaction(administrator=True)

    await CogManagementCog.manage_cogs.callback(CogManagementCog(bot), interaction, "unload", "cogs.search")

    bot.unload_extension.assert_not_awaited()
    assert "bot owner" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_owner_can_reload():
    bot = Mock(is_owner=AsyncMock(return_value=True), extensions={})
    bot.reload_extension = AsyncMock()
    interaction = _interaction(administrator=False)

    await CogManagementCog.manage_cogs.callback(CogManagementCog(bot), interaction, "reload", "cogs.search")

    bot.reload_extension.assert_awaited_once_with("cogs.search")
