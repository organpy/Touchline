"""Trade asset labels need one pick query however many picks are traded."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from cogs.transactions.trade_utils import enrich_trade_assets


def _supabase(picks):
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.in_.return_value.execute.return_value = Mock(data=picks)
    return supabase


@pytest.mark.asyncio
async def test_all_picks_are_labelled_from_one_query():
    supabase = _supabase([{'id': 'a', 'round': 1, 'overall': 3}, {'id': 'b', 'round': 2, 'overall': 14}])
    assets = [
        {'asset_type': 'pick', 'draft_pick_id': 'a'},
        {'asset_type': 'pick', 'draft_pick_id': 'b'},
        {'asset_type': 'pick', 'draft_pick_id': 'gone'},
    ]

    labels = [a['label'] for a in await enrich_trade_assets(assets, supabase)]

    assert labels == ['R1P03', 'R2P14', 'Unknown Pick']
    supabase.table.assert_called_once_with('draft_picks')
    supabase.table.return_value.select.return_value.in_.assert_called_once_with('id', ['a', 'b', 'gone'])


@pytest.mark.asyncio
async def test_players_use_the_cache_and_trades_without_picks_run_no_query():
    supabase = _supabase([])
    with patch('cogs.transactions.trade_utils.TeamPlayerCache') as cache:
        cache.return_value.get_player.return_value = {'display_name': 'Ace'}
        labels = [a['label'] for a in await enrich_trade_assets([{'asset_type': 'player', 'player_id': 'p1'}], supabase)]

    assert labels == ['Ace']
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_pick_query_labels_picks_unknown_instead_of_failing():
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.in_.return_value.execute.side_effect = ConnectionError("down")

    labels = [a['label'] for a in await enrich_trade_assets([{'asset_type': 'pick', 'draft_pick_id': 'a'}], supabase)]

    assert labels == ['Unknown Pick']
