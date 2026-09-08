"""Permission tests for /force-trade."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.admin import force_trade as force_trade_module
from cogs.admin.force_trade import ForceTradeCommand


def _make_interaction(*, administrator: bool, role_ids=()):
    interaction = Mock()
    interaction.guild = Mock(id=987654321)
    interaction.guild_id = 987654321
    interaction.channel_id = 111
    interaction.user = Mock(id=123)
    interaction.user.guild_permissions = Mock(administrator=administrator)
    interaction.user.roles = [Mock(id=role_id) for role_id in role_ids]
    interaction.response = Mock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = Mock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def cog():
    with patch.object(force_trade_module, "get_supabase", return_value=Mock()):
        yield ForceTradeCommand(Mock())


@pytest.mark.asyncio
async def test_non_admin_is_denied(cog):
    interaction = _make_interaction(administrator=False)

    with patch.object(
        force_trade_module.ConfigUtility, "get_guild_config", AsyncMock(return_value={})
    ), patch.object(
        force_trade_module.ConfigUtility, "send_permission_denied_message", AsyncMock()
    ) as denied:
        await ForceTradeCommand.force_trade.callback(cog, interaction, "AAA", "BBB")

    denied.assert_awaited_once_with(interaction)
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_role_is_allowed(cog):
    interaction = _make_interaction(administrator=False, role_ids=(555,))

    with patch.object(
        force_trade_module.ConfigUtility,
        "get_guild_config",
        AsyncMock(return_value={"admin_role_id": "555"}),
    ), patch.object(force_trade_module, "get_supabase", return_value=None):
        await ForceTradeCommand.force_trade.callback(cog, interaction, "AAA", "BBB")

    interaction.response.defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dm_is_rejected(cog):
    interaction = _make_interaction(administrator=True)
    interaction.guild = None

    await ForceTradeCommand.force_trade.callback(cog, interaction, "AAA", "BBB")

    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
