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
    def test_calls_rpc_and_returns_moved_count(self):
        supabase = _rpc_supabase(data=3)

        moved = TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert moved == 3
        supabase.rpc.assert_called_once_with(
            'execute_trade_proposal', {'p_proposal_id': "prop-1", 'p_executed_by': 42}
        )

    @pytest.mark.parametrize("hint", [code for code in TRADE_ERROR_MESSAGES if code != 'TRADE_FAILED'])
    def test_database_refusal_maps_hint_to_code(self, hint):
        supabase = _rpc_supabase(error=APIError({'message': 'nope', 'code': 'P0001', 'hint': hint}))

        with pytest.raises(TradeExecutionError) as exc_info:
            TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == hint
        assert exc_info.value.user_message == TRADE_ERROR_MESSAGES[hint]

    def test_unknown_hint_becomes_generic_failure(self):
        supabase = _rpc_supabase(error=APIError({'message': 'boom', 'code': '23505', 'hint': None}))

        with pytest.raises(TradeExecutionError) as exc_info:
            TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == 'TRADE_FAILED'

    def test_network_error_becomes_generic_failure(self):
        supabase = _rpc_supabase(error=ConnectionError("down"))

        with pytest.raises(TradeExecutionError) as exc_info:
            TransactionsRepository(supabase).execute_trade_proposal("prop-1", 42)

        assert exc_info.value.code == 'TRADE_FAILED'


class TestResolvePendingTrade:
    def _supabase(self, data):
        supabase = MagicMock()
        query = supabase.table.return_value.update.return_value
        query.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=data)
        return supabase

    def test_returns_true_when_pending_row_updated(self):
        supabase = self._supabase([{'id': 'prop-1', 'status': 'declined'}])

        assert TransactionsRepository(supabase).resolve_pending_trade("prop-1", 'declined', reviewed_by=7)

        payload = supabase.table.return_value.update.call_args.args[0]
        assert payload['status'] == 'declined'
        assert payload['reviewed_by'] == 7
        first_eq = supabase.table.return_value.update.return_value.eq
        first_eq.assert_called_once_with('id', "prop-1")
        first_eq.return_value.eq.assert_called_once_with('status', 'pending')

    def test_returns_false_when_already_resolved(self):
        supabase = self._supabase([])

        assert not TransactionsRepository(supabase).resolve_pending_trade("prop-1", 'declined')
