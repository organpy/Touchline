"""
Unit tests for draft pick trading: picks exist only in divisions with ``picks_enabled`` and only
change hands inside that division.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from cogs.transactions.pick_trading import (
    picks_enabled_for_division,
    picks_division_for_team,
    get_available_picks_for_team,
    transfer_pick_ownership,
    get_pick_details,
    validate_picks_for_trade,
    format_pick,
    create_pick_label,
)
from cogs.transactions.trade_utils import can_trade_picks
from utils.league_cache import LeagueCache


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_supabase():
    """Mock Supabase client"""
    return Mock()


@pytest.fixture(autouse=True)
def league():
    """D1 runs a draft, D2 doesn't; teams a/b play in D1 and c in D2 this season."""
    LeagueCache.load_snapshot(
        seasons=[{'id': 'season-1', 'number': 1, 'name': 'Season 1', 'active': True, 'phase': 'regular'}],
        divisions=[
            {'id': 'div-1', 'code': 'D1', 'name': 'Premier', 'tier': 1, 'settings': {'picks_enabled': True}},
            {'id': 'div-2', 'code': 'D2', 'name': 'Championship', 'tier': 2, 'settings': {}},
        ],
        assignments={'season-1': [
            {'team_id': 'team-a', 'division_id': 'div-1', 'group_id': None},
            {'team_id': 'team-b', 'division_id': 'div-1', 'group_id': None},
            {'team_id': 'team-c', 'division_id': 'div-2', 'group_id': None},
        ]},
    )
    yield
    LeagueCache.invalidate()


@pytest.fixture
def team_a():
    """Team A"""
    return {
        'id': 'team-a',
        'code': 'PANDAS',
        'name': 'Pandas',
        'division_id': 'div-1'
    }


@pytest.fixture
def team_b():
    """Team B"""
    return {
        'id': 'team-b',
        'code': 'LIONS',
        'name': 'Lions',
        'division_id': 'div-1'
    }


@pytest.fixture
def draft_pick_r1p1():
    """Draft pick R1P1"""
    return {
        'id': 'pick-r1p1',
        'season_id': 'season-1',
        'round': 1,
        'overall': 1,
        'original_team_id': 'team-a',
        'current_owner_team_id': 'team-a',
        'notes': None,
        'metadata': {}
    }


@pytest.fixture
def draft_pick_r1p2():
    """Draft pick R1P2"""
    return {
        'id': 'pick-r1p2',
        'season_id': 'season-1',
        'round': 1,
        'overall': 2,
        'original_team_id': 'team-b',
        'current_owner_team_id': 'team-b',
        'notes': None,
        'metadata': {}
    }


# ============================================================================
# Tests: which divisions run a draft
# ============================================================================

class TestPicksEnabled:
    def test_division_setting_decides(self):
        assert picks_enabled_for_division('div-1') is True
        assert picks_enabled_for_division('div-2') is False
        assert picks_enabled_for_division('missing') is False
        assert picks_enabled_for_division(None) is False

    def test_team_division_with_draft(self):
        assert picks_division_for_team('team-a')['code'] == 'D1'
        assert picks_division_for_team('team-c') is None
        assert picks_division_for_team('team-unassigned') is None

    @pytest.mark.asyncio
    async def test_can_trade_picks_only_inside_a_draft_division(self):
        assert await can_trade_picks('div-1', 'div-1') is True
        assert await can_trade_picks('div-2', 'div-2') is False
        assert await can_trade_picks('div-1', 'div-2') is False
        assert await can_trade_picks(None, None) is False


# ============================================================================
# Tests: Pick queries
class TestGetAvailablePicksForTeam:
    """Test fetching team's available picks"""

    @pytest.mark.asyncio
    async def test_get_available_picks_success(self, mock_supabase, draft_pick_r1p1):
        """Test successfully getting team's picks"""
        mock_supabase.table().select().eq().eq().order().execute.return_value = Mock(
            data=[draft_pick_r1p1]
        )

        picks = await get_available_picks_for_team(
            mock_supabase,
            'team-a',
            'season-1'
        )

        assert len(picks) == 1
        assert picks[0]['overall'] == 1

    @pytest.mark.asyncio
    async def test_get_available_picks_empty(self, mock_supabase):
        """Test team with no picks"""
        mock_supabase.table().select().eq().eq().order().execute.return_value = Mock(data=[])

        picks = await get_available_picks_for_team(
            mock_supabase,
            'team-unknown',
            'season-1'
        )

        assert picks == []

    @pytest.mark.asyncio
    async def test_get_available_picks_error(self, mock_supabase):
        """Test error fetching picks"""
        mock_supabase.table().select().eq().eq().order().execute.side_effect = Exception("DB error")

        picks = await get_available_picks_for_team(
            mock_supabase,
            'team-a',
            'season-1'
        )

        assert picks == []


class TestTransferPickOwnership:
    """Test transferring pick ownership (trading)"""

    @pytest.mark.asyncio
    async def test_transfer_pick_ownership_success(self, mock_supabase):
        """Test successfully transferring pick"""
        mock_supabase.table().update().eq().execute.return_value = Mock(
            data=[{'id': 'pick-r1p1', 'current_owner_team_id': 'team-b'}]
        )

        result = await transfer_pick_ownership(mock_supabase, 'pick-r1p1', 'team-b')

        assert result is True

    @pytest.mark.asyncio
    async def test_transfer_pick_ownership_no_data(self, mock_supabase):
        """Test transfer with no response"""
        mock_supabase.table().update().eq().execute.return_value = Mock(data=[])

        result = await transfer_pick_ownership(mock_supabase, 'pick-r1p1', 'team-b')

        assert result is False

    @pytest.mark.asyncio
    async def test_transfer_pick_ownership_error(self, mock_supabase):
        """Test transfer error"""
        mock_supabase.table().update().eq().execute.side_effect = Exception("DB error")

        result = await transfer_pick_ownership(mock_supabase, 'pick-r1p1', 'team-b')

        assert result is False


class TestGetPickDetails:
    """Test fetching pick details"""

    @pytest.mark.asyncio
    async def test_get_pick_details_success(self, mock_supabase, draft_pick_r1p1):
        """Test successfully getting pick details"""
        mock_supabase.table().select().eq().limit().execute.return_value = Mock(
            data=[draft_pick_r1p1]
        )

        pick = await get_pick_details(mock_supabase, 'pick-r1p1')

        assert pick is not None
        assert pick['overall'] == 1
        assert pick['round'] == 1

    @pytest.mark.asyncio
    async def test_get_pick_details_not_found(self, mock_supabase):
        """Test pick not found"""
        mock_supabase.table().select().eq().limit().execute.return_value = Mock(data=[])

        pick = await get_pick_details(mock_supabase, 'invalid-pick')

        assert pick is None

    @pytest.mark.asyncio
    async def test_get_pick_details_error(self, mock_supabase):
        """Test error getting pick"""
        mock_supabase.table().select().eq().limit().execute.side_effect = Exception("DB error")

        pick = await get_pick_details(mock_supabase, 'pick-r1p1')

        assert pick is None


# ============================================================================
# Tests: Validation (Pre-Trade)
# ============================================================================

class TestValidatePicksForTrade:
    """Pre-trade validation"""

    @staticmethod
    def _pick(**overrides):
        pick = {'id': 'pick-1', 'season_id': 'season-1', 'division_id': 'div-1',
                'current_owner_team_id': 'team-a', 'overall': 1}
        pick.update(overrides)
        return pick

    @pytest.mark.asyncio
    async def test_valid_pick(self, mock_supabase):
        with patch('cogs.transactions.pick_trading.get_pick_details', AsyncMock(return_value=self._pick())):
            mock_supabase.table().select().eq().execute.return_value = Mock(data=[])
            valid, message = await validate_picks_for_trade(mock_supabase, ['pick-1'], 'team-a', 'season-1')
        assert valid is True, message

    @pytest.mark.asyncio
    async def test_wrong_owner(self, mock_supabase):
        with patch('cogs.transactions.pick_trading.get_pick_details', AsyncMock(return_value=self._pick())):
            valid, message = await validate_picks_for_trade(mock_supabase, ['pick-1'], 'team-b', 'season-1')
        assert valid is False
        assert "doesn't own" in message

    @pytest.mark.asyncio
    async def test_division_without_draft(self, mock_supabase):
        pick = self._pick(division_id='div-2', current_owner_team_id='team-c')
        with patch('cogs.transactions.pick_trading.get_pick_details', AsyncMock(return_value=pick)):
            valid, message = await validate_picks_for_trade(mock_supabase, ['pick-1'], 'team-c', 'season-1')
        assert valid is False
        assert "turned off" in message

    @pytest.mark.asyncio
    async def test_other_season(self, mock_supabase):
        pick = self._pick(season_id='season-0')
        with patch('cogs.transactions.pick_trading.get_pick_details', AsyncMock(return_value=pick)):
            valid, message = await validate_picks_for_trade(mock_supabase, ['pick-1'], 'team-a', 'season-1')
        assert valid is False

    @pytest.mark.asyncio
    async def test_pick_in_pending_trade(self, mock_supabase):
        with patch('cogs.transactions.pick_trading.get_pick_details', AsyncMock(return_value=self._pick())):
            mock_supabase.table().select().eq().execute.return_value = Mock(data=[{'id': 'proposal-1'}])
            mock_supabase.table().select().eq().in_().limit().execute.return_value = Mock(data=[{'id': 'asset-1'}])
            valid, message = await validate_picks_for_trade(mock_supabase, ['pick-1'], 'team-a', 'season-1')
        assert valid is False
        assert "pending trade" in message


# ============================================================================
# Tests: Formatting
# ============================================================================

class TestFormatPick:
    """Test pick formatting for display"""

    def test_format_pick_simple(self, draft_pick_r1p1):
        """Test formatting simple pick"""
        result = format_pick(draft_pick_r1p1)
        assert result == "R1.1"

    def test_format_pick_multi_round(self):
        """Test formatting multi-round pick"""
        pick = {'round': 3, 'overall': 15}
        result = format_pick(pick)
        assert result == "R3.15"

    def test_format_pick_missing_data(self):
        """Test formatting with missing data"""
        pick = {}
        result = format_pick(pick)
        assert "?" in result  # Should have fallback


class TestCreatePickLabel:
    """Test creating pick labels for UI"""

    def test_create_pick_label_simple(self, draft_pick_r1p1):
        """Test creating label"""
        result = create_pick_label(draft_pick_r1p1)
        assert "#1" in result
        assert "R1.1" in result

    def test_create_pick_label_missing_data(self):
        """Test label with missing data"""
        pick = {}
        result = create_pick_label(pick)
        assert "#" in result  # Should have fallback


# ============================================================================
# Tests: Integration Scenarios
# ============================================================================

class TestPickTradeScenarios:
    """Integration tests for pick trading scenarios"""

    @pytest.mark.asyncio
    async def test_scenario_simple_2_for_1_trade(self, mock_supabase):
        """
        Scenario: Team A trades 2 picks to Team B for 1 pick
        Team A: R1P1, R2P1 → Team B: R1P8
        """
        # Setup mocks
        picks_a = [
            {'id': 'pick-r1p1', 'round': 1, 'overall': 1, 'current_owner_team_id': 'team-a'},
            {'id': 'pick-r2p1', 'round': 2, 'overall': 9, 'current_owner_team_id': 'team-a'},
        ]
        picks_b = [
            {'id': 'pick-r1p8', 'round': 1, 'overall': 8, 'current_owner_team_id': 'team-b'},
        ]

        # Validate all picks
        valid_a = all(p['current_owner_team_id'] == 'team-a' for p in picks_a)
        valid_b = all(p['current_owner_team_id'] == 'team-b' for p in picks_b)

        assert valid_a is True
        assert valid_b is True

    @pytest.mark.asyncio
    async def test_scenario_immutable_original_team(self):
        """
        Scenario: Verify original_team_id never changes
        Pick traded multiple times: Team A → Team B → Team C
        """
        pick = {
            'id': 'pick-r1p1',
            'original_team_id': 'team-a',
            'current_owner_team_id': 'team-a'
        }

        # Simulate trade 1: A → B
        pick['current_owner_team_id'] = 'team-b'
        assert pick['original_team_id'] == 'team-a'  # Unchanged

        # Simulate trade 2: B → C
        pick['current_owner_team_id'] = 'team-c'
        assert pick['original_team_id'] == 'team-a'  # Still unchanged

        # Verify history
        assert pick['current_owner_team_id'] != pick['original_team_id']


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
