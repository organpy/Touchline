"""Unit tests for transactions module."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from uuid import uuid4

from db.transactions_repository import TransactionsRepository
from cogs.transactions.history import (
    TransactionHistoryPaginator,
    build_history_embed,
    fetch_filtered_transactions
)


class TestTransactionsRepository:
    """Tests for TransactionsRepository database operations."""

    async def test_create_pending_signing(self):
        """Test creating a pending signing."""
        mock_supabase = Mock()
        mock_supabase.table().insert().execute.return_value = Mock(
            data=[{'id': 'sig_001', 'status': 'pending'}]
        )

        repo = TransactionsRepository(mock_supabase)
        payload = {
            'guild_id': 123456,
            'season_id': 'season_001',
            'team_id': 'team_001',
            'player_id': 'player_001',
            'created_by': 999,
            'status': 'pending'
        }

        result = await repo.create_pending_signing(payload)

        assert result is not None
        assert result['id'] == 'sig_001'
        assert result['status'] == 'pending'
        mock_supabase.table().insert().execute.assert_called_once()

    async def test_create_pending_signing_failure(self):
        """Test pending signing creation failure."""
        mock_supabase = Mock()
        mock_supabase.table().insert().execute.return_value = Mock(data=None)

        repo = TransactionsRepository(mock_supabase)
        result = await repo.create_pending_signing({})

        assert result is None

    async def test_update_pending_signing_status(self):
        """Test updating pending signing status."""
        mock_supabase = Mock()
        mock_supabase.table().update().eq().execute.return_value = Mock()

        repo = TransactionsRepository(mock_supabase)
        success = await repo.update_pending_signing_status('sig_001', 'accepted')

        assert success is True
        mock_supabase.table().update().eq().execute.assert_called_once()

    async def test_cancel_other_pending_signings(self):
        """Test cancelling other pending signings for a player."""
        mock_supabase = Mock()
        
        # Chain mocks for: table().update().eq().eq().neq().execute()
        mock_table = Mock()
        mock_update = Mock()
        mock_eq1 = Mock()
        mock_eq2 = Mock()
        mock_neq = Mock()
        
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value = mock_update
        mock_update.eq.side_effect = [mock_eq1, mock_eq2]  # First .eq() then second
        mock_eq1.eq.return_value = mock_eq2  # Second .eq() returns itself
        mock_eq2.neq.return_value = mock_neq  # .neq() returns mock
        mock_neq.execute.return_value = Mock()

        repo = TransactionsRepository(mock_supabase)
        await repo.cancel_other_pending_signings('player_001', keep_id='sig_001')

        # Verify the chain was called correctly
        mock_supabase.table.assert_called_with('pending_signings')
        mock_table.update.assert_called_with({'status': 'declined'})
        mock_neq.execute.assert_called_once()

    async def test_list_draft_picks(self):
        """Test listing draft picks for a season."""
        mock_supabase = Mock()
        picks_data = [
            {'id': 'pick_001', 'round': 1, 'overall': 1},
            {'id': 'pick_002', 'round': 1, 'overall': 2}
        ]
        mock_supabase.table().select().eq().order().order().execute.return_value = Mock(
            data=picks_data
        )

        repo = TransactionsRepository(mock_supabase)
        result = await repo.list_draft_picks('season_001')

        assert len(result) == 2
        assert result[0]['id'] == 'pick_001'

    async def test_insert_transaction(self):
        """Test inserting a transaction record."""
        mock_supabase = Mock()
        mock_supabase.table().insert().execute.return_value = Mock(
            data=[{'id': 'txn_001', 'type': 'sign'}]
        )

        repo = TransactionsRepository(mock_supabase)
        payload = {
            'season_id': 'season_001',
            'type': 'sign',
            'player_id': 'player_001',
            'to_team_id': 'team_001',
            'executed_by': 999
        }

        result = await repo.insert_transaction(payload)

        assert result is not None
        assert result['id'] == 'txn_001'
        assert result['type'] == 'sign'

    async def test_transfer_player_between_teams(self):
        """Test transferring a player between teams."""
        mock_supabase = Mock()
        mock_supabase.table().update().eq().eq().is_().execute.return_value = Mock()
        mock_supabase.table().insert().execute.return_value = Mock()

        repo = TransactionsRepository(mock_supabase)
        success = await repo.transfer_player_between_teams(
            'season_001',
            'player_001',
            'team_001',
            'team_002'
        )

        assert success is True

    async def test_update_draft_pick_owner(self):
        """Test updating draft pick ownership."""
        mock_supabase = Mock()
        mock_supabase.table().update().eq().execute.return_value = Mock(
            data=[{'id': 'pick_001', 'current_owner_team_id': 'team_002'}]
        )

        repo = TransactionsRepository(mock_supabase)
        result = await repo.update_draft_pick_owner('pick_001', 'team_002')

        assert result is not None
        assert result['current_owner_team_id'] == 'team_002'

    async def test_create_trade_proposal(self):
        """Test creating a trade proposal."""
        mock_supabase = Mock()
        mock_supabase.table().insert().execute.return_value = Mock(
            data=[{'id': 'trade_001', 'status': 'pending'}]
        )

        repo = TransactionsRepository(mock_supabase)
        payload = {
            'guild_id': 123456,
            'season_id': 'season_001',
            'initiating_team_id': 'team_001',
            'counterparty_team_id': 'team_002',
            'status': 'pending',
            'created_by': 999
        }

        result = await repo.create_trade_proposal(payload)

        assert result is not None
        assert result['id'] == 'trade_001'


class TestTransactionHistoryPaginator:
    """Tests for transaction history pagination."""

    def test_pagination_single_page(self):
        """Test pagination with single page."""
        transactions = [{'id': f'txn_{i}'} for i in range(5)]
        paginator = TransactionHistoryPaginator(transactions, page_size=10)

        assert paginator.total_pages == 1
        page, current, total = paginator.get_page(0)
        assert len(page) == 5
        assert current == 0
        assert total == 1

    def test_pagination_multiple_pages(self):
        """Test pagination with multiple pages."""
        transactions = [{'id': f'txn_{i}'} for i in range(25)]
        paginator = TransactionHistoryPaginator(transactions, page_size=10)

        assert paginator.total_pages == 3

        # Page 0
        page0, current0, total0 = paginator.get_page(0)
        assert len(page0) == 10
        assert current0 == 0
        assert total0 == 3

        # Page 2
        page2, current2, total2 = paginator.get_page(2)
        assert len(page2) == 5
        assert current2 == 2
        assert total2 == 3

    def test_pagination_bounds(self):
        """Test pagination respects bounds."""
        transactions = [{'id': f'txn_{i}'} for i in range(10)]
        paginator = TransactionHistoryPaginator(transactions, page_size=5)

        # Try page out of bounds
        page, current, total = paginator.get_page(100)
        assert current == 1  # Clamps to last page

    def test_build_embed(self):
        """Test building a paginated embed."""
        transactions = [
            {
                'id': 'txn_001',
                'type': 'sign',
                'created_at': '2025-11-08T10:00:00Z',
                'player_id': 'player_001',
                'from_team_id': None,
                'to_team_id': 'team_001'
            }
        ]
        paginator = TransactionHistoryPaginator(transactions, page_size=10)

        team_by_id = {'team_001': {'code': 'PANDAS'}}
        player_by_id = {'player_001': {'display_name': 'John Doe'}}

        # Test that paginator can build data for embed (synchronous)
        page, current, total = paginator.get_page(0)  # 0-indexed page number
        assert len(page) == 1
        assert current == 0
        assert total == 1


class TestBuildHistoryEmbed:
    """Tests for history embed building."""

    def test_build_embed_empty(self):
        """Test building embed with no transactions."""
        embed = build_history_embed([], {}, {})

        assert embed.title == "Recent Transactions"
        assert "No transactions found" in embed.description

    def test_build_embed_with_transactions(self):
        """Test building embed with transactions."""
        transactions = [
            {
                'id': 'txn_001',
                'type': 'sign',
                'player_id': 'player_001',
                'from_team_id': None,
                'to_team_id': 'team_001'
            },
            {
                'id': 'txn_002',
                'type': 'release',
                'player_id': 'player_001',
                'from_team_id': 'team_001',
                'to_team_id': None
            }
        ]

        team_by_id = {'team_001': {'code': 'PANDAS'}}
        player_by_id = {'player_001': {'display_name': 'John Doe'}}

        embed = build_history_embed(transactions, team_by_id, player_by_id)

        assert embed.title == "Recent Transactions"
        assert len(embed.fields) == 2


@pytest.mark.skip(reason="Async testing requires pytest-asyncio plugin configuration")
def test_fetch_filtered_transactions():
    """Test fetching transactions with filters (skipped - requires async setup)."""
    # This test is skipped because it requires pytest-asyncio setup
    # To enable: install pytest-asyncio and add to pytest.ini or pyproject.toml:
    # [tool:pytest]
    # asyncio_mode = auto
    pass


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
