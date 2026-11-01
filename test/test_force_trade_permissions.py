"""Permission tests for /force-trade."""

from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from cogs.admin import force_trade as force_trade_module
from cogs.admin.force_trade import ForceTradeCommand


def _make_interaction(*, administrator: bool, role_ids=()):
    interaction = Mock()
    interaction.guild = Mock(id=987654321)
    interaction.guild_id = 987654321
    interaction.channel_id = 111
    interaction.user = Mock(spec=discord.Member, id=123)
    interaction.user.guild_permissions = discord.Permissions(administrator=administrator)
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


async def _passes_checks(interaction):
    return all([await check(interaction) for check in ForceTradeCommand.force_trade.checks])


@pytest.mark.asyncio
async def test_non_admin_fails_the_command_check():
    interaction = _make_interaction(administrator=False)

    with patch.object(force_trade_module.ConfigUtility, "get_guild_config", AsyncMock(return_value={})):
        assert await _passes_checks(interaction) is False


@pytest.mark.asyncio
async def test_admin_role_passes_the_command_check():
    interaction = _make_interaction(administrator=False, role_ids=(555,))

    with patch.object(
        force_trade_module.ConfigUtility,
        "get_guild_config",
        AsyncMock(return_value={"admin_role_id": "555"}),
    ):
        assert await _passes_checks(interaction) is True


@pytest.mark.asyncio
async def test_admin_passes_and_runs(cog):
    interaction = _make_interaction(administrator=True)

    with patch.object(force_trade_module.ConfigUtility, "get_guild_config", AsyncMock(return_value={})), \
         patch.object(force_trade_module, "get_supabase", return_value=None):
        assert await _passes_checks(interaction) is True
        await ForceTradeCommand.force_trade.callback(cog, interaction, "AAA", "BBB")

    interaction.response.defer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dm_is_rejected(cog):
    interaction = _make_interaction(administrator=True)
    interaction.guild = None

    await ForceTradeCommand.force_trade.callback(cog, interaction, "AAA", "BBB")

    interaction.response.send_message.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


class TestForceTradeExecution:
    """The Execute button must go through the atomic database function."""

    def _view(self):
        view = force_trade_module.ForceTradeSelectionView(
            team1={'id': 'team-a', 'code': 'aaa', 'name': 'Team A'},
            team2={'id': 'team-b', 'code': 'bbb', 'name': 'Team B'},
            season={'id': 'season-1'},
            supabase=Mock(),
            team1_player_assets=[],
            team1_pick_assets=[],
            team2_player_assets=[],
            team2_pick_assets=[],
            initiator_discord_id=123,
            guild_id=987654321,
        )
        view.get_selected_assets = Mock(return_value={
            'outbound': [{'type': 'player', 'player_id': 'p1', 'label': 'Player One'}],
            'inbound': [{'type': 'pick', 'pick_id': 'k1', 'label': 'R1 #3'}],
        })
        return view

    def _repo(self):
        repo = AsyncMock()
        repo.create_trade_proposal.return_value = {'id': 'prop-1234567890'}
        repo.attach_trade_assets.return_value = [{'id': 'a1'}, {'id': 'a2'}]
        return repo

    @pytest.mark.asyncio
    async def test_execute_uses_atomic_function(self):
        view, repo = self._view(), self._repo()
        interaction = _make_interaction(administrator=True)

        with patch.object(force_trade_module, "TransactionsRepository", return_value=repo), patch.object(
            force_trade_module.TeamPlayerCache, "refresh", AsyncMock()
        ):
            await view.execute_trade.callback(interaction)

        assert repo.create_trade_proposal.call_args.args[0]['status'] == 'pending'
        saved_assets = repo.attach_trade_assets.call_args.args[0]
        assert [(a['direction'], a['asset_type'], a['player_id'], a['draft_pick_id']) for a in saved_assets] == [
            ('outbound', 'player', 'p1', None),
            ('inbound', 'pick', None, 'k1'),
        ]
        repo.execute_trade_proposal.assert_called_once_with('prop-1234567890', 123)
        repo.transfer_player_between_teams.assert_not_called()
        repo.update_draft_pick_owner.assert_not_called()

    @pytest.mark.asyncio
    async def test_refused_trade_is_cancelled(self):
        view, repo = self._view(), self._repo()
        repo.execute_trade_proposal.side_effect = force_trade_module.TradeExecutionError('TRADE_PICK_UNAVAILABLE')
        interaction = _make_interaction(administrator=True)

        with patch.object(force_trade_module, "TransactionsRepository", return_value=repo):
            await view.execute_trade.callback(interaction)

        repo.resolve_pending_trade.assert_called_once_with('prop-1234567890', 'cancelled', reviewed_by=123)
        assert "no longer owned" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_failed_asset_save_cancels_before_executing(self):
        view, repo = self._view(), self._repo()
        repo.attach_trade_assets.return_value = []
        interaction = _make_interaction(administrator=True)

        with patch.object(force_trade_module, "TransactionsRepository", return_value=repo):
            await view.execute_trade.callback(interaction)

        repo.execute_trade_proposal.assert_not_called()
        repo.resolve_pending_trade.assert_called_once_with('prop-1234567890', 'cancelled', reviewed_by=123)
