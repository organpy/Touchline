"""Tests for the persistent trade approval buttons."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cogs.transactions import trade_approval
from cogs.transactions.trade_approval import (
    TradeAcceptButton,
    TradeApprovalView,
    TradeContext,
    TradeDeclineButton,
)
from db.transactions_repository import TradeExecutionError

PROPOSAL_ID = "3f2b8c1e-1111-4222-8333-944455556666"
COUNTERPARTY_OWNER = 2002


def _context():
    return TradeContext(
        proposal={'id': PROPOSAL_ID, 'status': 'pending'},
        initiating_team={'id': 'team-a', 'code': 'aaa', 'name': 'Team A', 'owner_discord_id': 1001},
        counterparty_team={'id': 'team-b', 'code': 'bbb', 'name': 'Team B', 'owner_discord_id': COUNTERPARTY_OWNER},
        trade_assets=[{'direction': 'outbound', 'asset_type': 'player', 'player_id': 'p1'}],
        league_code='NBL',
    )


def _interaction(user_id=COUNTERPARTY_OWNER):
    interaction = Mock()
    interaction.user = Mock(id=user_id, mention=f"<@{user_id}>")
    interaction.response = Mock()
    interaction.response.defer = AsyncMock()
    interaction.followup = Mock()
    interaction.followup.send = AsyncMock()
    interaction.message = Mock(attachments=[])
    interaction.message.edit = AsyncMock()
    interaction.guild = Mock()
    return interaction


@pytest.fixture
def repo():
    repo = MagicMock()
    with patch.object(trade_approval, "get_supabase", return_value=Mock()), patch.object(
        trade_approval, "TransactionsRepository", return_value=repo
    ), patch.object(trade_approval, "load_trade_context", AsyncMock(return_value=_context())), patch.object(
        trade_approval, "build_acceptance_embed", AsyncMock(return_value=Mock())
    ), patch.object(
        trade_approval, "build_cancelled_embed", Mock(return_value=Mock())
    ), patch.object(
        trade_approval, "build_decline_embed", Mock(return_value=Mock())
    ), patch.object(
        trade_approval, "apply_team_badge"
    ), patch.object(
        trade_approval, "apply_league_badge"
    ):
        yield repo


class TestPersistence:
    def test_view_uses_proposal_scoped_custom_ids(self):
        view = TradeApprovalView(PROPOSAL_ID)

        custom_ids = sorted(item.custom_id for item in view.children)

        assert custom_ids == [f"trade:accept:{PROPOSAL_ID}", f"trade:decline:{PROPOSAL_ID}"]
        assert view.timeout is None

    @pytest.mark.parametrize("button_cls", [TradeAcceptButton, TradeDeclineButton])
    @pytest.mark.asyncio
    async def test_custom_id_round_trips_after_restart(self, button_cls):
        custom_id = button_cls(PROPOSAL_ID).custom_id
        match = button_cls.__discord_ui_compiled_template__.fullmatch(custom_id)

        rebuilt = await button_cls.from_custom_id(Mock(), Mock(), match)

        assert rebuilt.proposal_id == PROPOSAL_ID


class TestAccept:
    @pytest.mark.asyncio
    async def test_only_counterparty_owner_can_accept(self, repo):
        interaction = _interaction(user_id=9999)

        await TradeAcceptButton(PROPOSAL_ID).callback(interaction)

        repo.execute_trade_proposal.assert_not_called()
        interaction.message.edit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_executes_atomically_and_updates_message(self, repo):
        interaction = _interaction()
        repo.execute_trade_proposal.return_value = 1

        with patch.object(trade_approval, "_invalidate_trade_caches", AsyncMock()) as invalidate, patch.object(
            trade_approval, "post_trade_executed_announcement", AsyncMock()
        ) as announce:
            await TradeAcceptButton(PROPOSAL_ID).callback(interaction)

        repo.execute_trade_proposal.assert_called_once_with(PROPOSAL_ID, COUNTERPARTY_OWNER)
        invalidate.assert_awaited_once()
        announce.assert_awaited_once()
        assert interaction.message.edit.await_args.kwargs['view'] is None
        assert "executed" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_stale_asset_cancels_proposal(self, repo):
        interaction = _interaction()
        repo.execute_trade_proposal.side_effect = TradeExecutionError('TRADE_PLAYER_UNAVAILABLE')
        repo.resolve_pending_trade.return_value = True

        await TradeAcceptButton(PROPOSAL_ID).callback(interaction)

        repo.resolve_pending_trade.assert_called_once_with(PROPOSAL_ID, 'cancelled', reviewed_by=COUNTERPARTY_OWNER)
        interaction.message.edit.assert_awaited_once()
        assert "no longer on the expected team" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_already_resolved_does_not_cancel(self, repo):
        interaction = _interaction()
        repo.execute_trade_proposal.side_effect = TradeExecutionError('TRADE_NOT_PENDING')

        await TradeAcceptButton(PROPOSAL_ID).callback(interaction)

        repo.resolve_pending_trade.assert_not_called()
        interaction.message.edit.assert_not_awaited()
        assert "already been resolved" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_missing_proposal_is_reported(self, repo):
        interaction = _interaction()

        with patch.object(trade_approval, "load_trade_context", AsyncMock(return_value=None)):
            await TradeAcceptButton(PROPOSAL_ID).callback(interaction)

        repo.execute_trade_proposal.assert_not_called()
        assert "no longer exists" in interaction.followup.send.await_args.args[0]


class TestDecline:
    @pytest.mark.asyncio
    async def test_decline_resolves_only_pending_proposal(self, repo):
        interaction = _interaction()
        repo.resolve_pending_trade.return_value = True

        await TradeDeclineButton(PROPOSAL_ID).callback(interaction)

        repo.resolve_pending_trade.assert_called_once_with(PROPOSAL_ID, 'declined', reviewed_by=COUNTERPARTY_OWNER)
        assert interaction.message.edit.await_args.kwargs['view'] is None

    @pytest.mark.asyncio
    async def test_decline_after_execution_is_refused(self, repo):
        interaction = _interaction()
        repo.resolve_pending_trade.return_value = False

        await TradeDeclineButton(PROPOSAL_ID).callback(interaction)

        interaction.message.edit.assert_not_awaited()
        assert "already been resolved" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_only_counterparty_owner_can_decline(self, repo):
        interaction = _interaction(user_id=1001)

        await TradeDeclineButton(PROPOSAL_ID).callback(interaction)

        repo.resolve_pending_trade.assert_not_called()
