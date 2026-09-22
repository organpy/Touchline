"""Tests for trade execution helpers in TransactionsRepository."""

from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

from db.transactions_repository import (
    TRADE_ERROR_MESSAGES,
    TradeExecutionError,
    TransactionsRepository,
)


def _rpc_supabase(*, data=None, error=None):
    supabase = MagicMock()
    call = supabase.rpc.return_value.execute
    if error is not None:
        call.side_effect = error
    else:
        call.return_value = MagicMock(data=data)
    return supabase


class TestExecuteTradeProposal:
    async def test_calls_rpc_and_returns_moved_count(self):
        supabase = _rpc_supabase(data=3)

        moved = await TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert moved == 3
        supabase.rpc.assert_called_once_with(
            'execute_trade_proposal', {'p_proposal_id': "prop-1", 'p_executed_by': 42}
        )

    @pytest.mark.parametrize("hint", [code for code in TRADE_ERROR_MESSAGES if code != 'TRADE_FAILED'])
    async def test_database_refusal_maps_hint_to_code(self, hint):
        supabase = _rpc_supabase(error=APIError({'message': 'nope', 'code': 'P0001', 'hint': hint}))

        with pytest.raises(TradeExecutionError) as exc_info:
            await TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == hint
        assert exc_info.value.user_message == TRADE_ERROR_MESSAGES[hint]

    async def test_unknown_hint_becomes_generic_failure(self):
        supabase = _rpc_supabase(error=APIError({'message': 'boom', 'code': '23505', 'hint': None}))

        with pytest.raises(TradeExecutionError) as exc_info:
            await TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == 'TRADE_FAILED'

    async def test_network_error_becomes_generic_failure(self):
        supabase = _rpc_supabase(error=ConnectionError("down"))

        with pytest.raises(TradeExecutionError) as exc_info:
            await TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == 'TRADE_FAILED'


class TestResolvePendingTrade:
    def _supabase(self, data):
        supabase = MagicMock()
        query = supabase.table.return_value.update.return_value
        query.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=data)
        return supabase

    async def test_returns_true_when_pending_row_updated(self):
        supabase = self._supabase([{'id': 'prop-1', 'status': 'declined'}])

        assert await TransactionsRepository(supabase).resolve_pending_trade("prop-1", 'declined', reviewed_by=7)

        payload = supabase.table.return_value.update.call_args.args[0]
        assert payload['status'] == 'declined'
        assert payload['reviewed_by'] == 7
        first_eq = supabase.table.return_value.update.return_value.eq
        first_eq.assert_called_once_with('id', "prop-1")
        first_eq.return_value.eq.assert_called_once_with('status', 'pending')

    async def test_returns_false_when_already_resolved(self):
        supabase = self._supabase([])

        assert not await TransactionsRepository(supabase).resolve_pending_trade("prop-1", 'declined')


class TestInsertTransaction:
    async def _insert(self, payload):
        supabase = MagicMock()
        supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{'id': 'tx-1'}])
        result = await TransactionsRepository(supabase).insert_transaction(payload)
        row = supabase.table.return_value.insert.call_args_list[0].args[0]
        event = supabase.table.return_value.insert.call_args_list[1].args[0]
        return result, row, event

    async def test_release_with_table_column_names_is_recorded_correctly(self):
        result, row, event = await self._insert({
            'season_id': 's1',
            'type': 'release',
            'player_id': 'p1',
            'from_team_id': 'team-a',
            'to_team_id': None,
            'executed_by': 42,
            'details': {'reason': 'cut'},
        })

        assert result == {'id': 'tx-1'}
        assert row == {
            'season_id': 's1',
            'type': 'release',
            'player_id': 'p1',
            'from_team_id': 'team-a',
            'executed_by': 42,
            'details': {'reason': 'cut'},
        }
        assert event['event_type'] == 'release'

    async def test_signing_workflow_shape_is_mapped(self):
        _, row, event = await self._insert({
            'season_id': 's1',
            'transaction_type': 'admin_force_sign',
            'player_id': 'p1',
            'team_id': 'team-b',
            'metadata': {'from_team_id': None, 'admin_user_id': 7},
        })

        assert row['type'] == 'sign'
        assert row['to_team_id'] == 'team-b'
        assert row['executed_by'] == 7
        assert 'from_team_id' not in row
        assert event['event_type'] == 'admin_force_sign'
