"""Unit tests for stats commands with validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import discord
from db import queries
from utils.team_player_cache import TeamPlayerCache


# =====================================================================
# Fixtures for mocking
# =====================================================================

class FakeSupabase:
    """Mock Supabase client for testing."""
    
    def __init__(self, data=None):
        self.data = data or {}
    
    def table(self, table_name):
        return FakeTable(self.data.get(table_name, []))
    
    def rpc(self, func_name, params):
        return FakeRPC()


class FakeTable:
    """Mock Supabase table interface."""
    
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
    
    def select(self, columns):
        return self
    
    def eq(self, key, value):
        self.filters[key] = value
        return self
    
    def limit(self, n):
        return self
    
    def execute(self):
        # Simple filter matching
        result = self.rows
        for key, value in self.filters.items():
            result = [r for r in result if r.get(key) == value]
        return FakeResponse(result)
    
    def ilike(self, key, value):
        self.filters[key] = value
        return self
    
    def is_(self, key, value):
        return self
    
    def in_(self, key, values):
        return self
    
    def insert(self, data):
        return FakeInsert(data)


class FakeRPC:
    """Mock Supabase RPC function."""
    
    def execute(self):
        return FakeResponse([{'ppg': 15.5, 'apg': 3.2, 'gpg': 1.1}])


class FakeInsert:
    """Mock insert operation."""
    
    def __init__(self, data):
        self.data = data
    
    def execute(self):
        return FakeResponse([self.data] if isinstance(self.data, dict) else self.data)


class FakeResponse:
    """Mock Supabase response."""
    
    def __init__(self, data):
        self.data = data


@pytest.fixture
def mock_interaction():
    """Create a mock Discord interaction."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = AsyncMock()
    interaction.guild_id = 123456
    return interaction


@pytest.fixture
def mock_supabase():
    """Create mock Supabase client with sample data."""
    return FakeSupabase({
        'divisions': [
            {'id': 'div-1', 'league_type': 'NBL', 'name': 'NBL Prime'},
            {'id': 'div-2', 'league_type': 'NCBA', 'name': 'NCBA'},
        ],
        'seasons': [
            {'id': 'season-1', 'name': 'S10', 'division_id': 'div-1', 'active': True},
            {'id': 'season-2', 'name': 'S2', 'division_id': 'div-2', 'active': True},
        ],
        'teams': [
            {'id': 'team-1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div-1', 'owner_discord_id': 111},
            {'id': 'team-2', 'code': 'TIGERS', 'name': 'Tigers', 'division_id': 'div-1', 'owner_discord_id': 222},
        ],
        'players': [
            {'id': 'player-1', 'display_name': 'Ace', 'discord_id': 333},
            {'id': 'player-2', 'display_name': 'Bot', 'discord_id': 444},
        ],
        'rosters': [
            # Ace on Pandas in S10
            {'id': 'roster-1', 'season_id': 'season-1', 'team_id': 'team-1', 'player_id': 'player-1', 'joined_at': '2025-01-01', 'left_at': None},
            # Bot on Tigers in S10
            {'id': 'roster-2', 'season_id': 'season-1', 'team_id': 'team-2', 'player_id': 'player-2', 'joined_at': '2025-01-01', 'left_at': None},
        ],
    })


@pytest.fixture
def stats_cog(mock_supabase):
    """Create StatsCog with mocked Supabase."""
    from cogs.stats.stats_commands import StatsCog
    
    with patch('cogs.stats.stats_commands.get_supabase', return_value=mock_supabase):
        bot = AsyncMock()
        cog = StatsCog(bot)
        cog.supabase = mock_supabase
        return cog


# =====================================================================
# Tests for validation methods
# =====================================================================

def test_validate_player_in_season_valid(stats_cog, mock_supabase):
    """Test validation passes for player with roster entry in season."""
    import asyncio
    
    player = {'id': 'player-1', 'display_name': 'Ace'}
    season = {'id': 'season-1', 'name': 'S10'}
    
    result = asyncio.run(stats_cog._validate_player_in_season(player, season))
    assert result is True


def test_validate_player_in_season_invalid(stats_cog, mock_supabase):
    """Test validation fails for player without roster entry in season."""
    import asyncio
    
    player = {'id': 'player-1', 'display_name': 'Ace'}
    season = {'id': 'season-2', 'name': 'S2'}  # Different season where Ace not on roster
    
    result = asyncio.run(stats_cog._validate_player_in_season(player, season))
    assert result is False


def test_validate_player_in_season_missing_player(stats_cog):
    """Test validation fails with missing player."""
    import asyncio
    
    result = asyncio.run(stats_cog._validate_player_in_season(None, {'id': 'season-1'}))
    assert result is False


def test_validate_team_in_season_valid(stats_cog):
    """Test validation passes for team in correct division."""
    import asyncio
    
    team = {'id': 'team-1', 'division_id': 'div-1'}
    season = {'id': 'season-1', 'division_id': 'div-1'}
    
    result = asyncio.run(stats_cog._validate_team_in_season(team, season))
    assert result is True


def test_validate_team_in_season_invalid_division(stats_cog):
    """Test validation fails for team in wrong division."""
    import asyncio
    
    team = {'id': 'team-1', 'division_id': 'div-1'}
    season = {'id': 'season-2', 'division_id': 'div-2'}  # Different division
    
    result = asyncio.run(stats_cog._validate_team_in_season(team, season))
    assert result is False


def test_validate_team_in_season_missing_data(stats_cog):
    """Test validation fails with missing data."""
    import asyncio
    
    result = asyncio.run(stats_cog._validate_team_in_season(None, {'id': 'season-1'}))
    assert result is False


# =====================================================================
# Tests for average command
# =====================================================================

def test_average_player_not_found(stats_cog, mock_interaction):
    """Test /average command when player not found."""
    import asyncio
    
    async def run_test():
        with patch.object(stats_cog, '_resolve_player', return_value=None):
            # Call the underlying callback
            await stats_cog.average.callback(stats_cog, mock_interaction, 'NonExistent')
            
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args[0][0]
            assert '❌' in call_args
            assert 'not found' in call_args.lower()
    
    asyncio.run(run_test())


def test_average_season_not_found(stats_cog, mock_interaction):
    """Test /average command when season not found."""
    import asyncio
    
    async def run_test():
        player = {'id': 'player-1', 'display_name': 'Ace', 'active_memberships': [], 'memberships_by_season': {}}
        
        with patch.object(stats_cog, '_resolve_player', return_value=player):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(None, None)):
                await stats_cog.average(mock_interaction, 'Ace')
                
                call_args = mock_interaction.followup.send.call_args[0][0]
                assert 'Could not locate a season' in call_args
    
    asyncio.run(run_test())


def test_average_player_not_in_season(stats_cog, mock_interaction):
    """Test /average command when player not in season."""
    import asyncio
    
    async def run_test():
        player = {'id': 'player-1', 'display_name': 'Ace'}
        season = {'id': 'season-2', 'name': 'S2', 'division_id': 'div-2'}
        league = {'code': 'NCBA', 'name': 'NCBA'}
        
        with patch.object(stats_cog, '_resolve_player', return_value=player):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_player_in_season', return_value=False):
                    await stats_cog.average(mock_interaction, 'Ace')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert '❌' in call_args
                    assert 'not on any roster' in call_args
    
    asyncio.run(run_test())


def test_average_success(stats_cog, mock_interaction):
    """Test /average command with valid player and season."""
    import asyncio
    
    async def run_test():
        player = {'id': 'player-1', 'display_name': 'Ace', 'active_memberships': [], 'memberships_by_season': {}}
        season = {'id': 'season-1', 'name': 'S10', 'division_id': 'div-1'}
        league = {'code': 'NBL', 'name': 'NBL Prime'}
        averages = {'ppg': 15.5, 'apg': 3.2, 'games_played': 10}
        
        with patch.object(stats_cog, '_resolve_player', return_value=player):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_player_in_season', return_value=True):
                    with patch.object(stats_cog, '_select_player_membership', return_value=None):
                        with patch.object(queries, 'get_player_averages', new_callable=AsyncMock, return_value=averages):
                            await stats_cog.average(mock_interaction, 'Ace')
                            
                            # Should send embed (not error)
                            assert mock_interaction.followup.send.called
                            call_kwargs = mock_interaction.followup.send.call_args[1]
                            assert 'embed' in call_kwargs
    
    asyncio.run(run_test())


# =====================================================================
# Tests for total-stats command
# =====================================================================

def test_total_stats_player_not_in_season(stats_cog, mock_interaction):
    """Test /total-stats command rejects player not in season."""
    import asyncio
    
    async def run_test():
        player = {'id': 'player-1', 'display_name': 'Ace'}
        season = {'id': 'season-2', 'name': 'S2', 'division_id': 'div-2'}
        league = {'code': 'NCBA', 'name': 'NCBA'}
        
        with patch.object(stats_cog, '_resolve_player', return_value=player):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_player_in_season', return_value=False):
                    await stats_cog.total_stats(mock_interaction, 'Ace')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert 'not on any roster' in call_args
    
    asyncio.run(run_test())


# =====================================================================
# Tests for team-stats command
# =====================================================================

def test_team_stats_team_not_found(stats_cog, mock_interaction):
    """Test /team-stats when team not found."""
    import asyncio
    
    async def run_test():
        cache = MagicMock()
        cache.get_team_by_code.return_value = None
        
        with patch.object(TeamPlayerCache, '__new__', return_value=cache):
            await stats_cog.team_stats(mock_interaction, 'FAKE')
            
            call_args = mock_interaction.followup.send.call_args[0][0]
            assert 'not found' in call_args.lower()
    
    asyncio.run(run_test())


def test_team_stats_team_not_in_season(stats_cog, mock_interaction):
    """Test /team-stats when team not in season."""
    import asyncio
    
    async def run_test():
        team = {'id': 'team-1', 'name': 'Pandas', 'code': 'PANDAS', 'division_id': 'div-1', 'league_type': 'NBL'}
        season = {'id': 'season-2', 'name': 'S2', 'division_id': 'div-2'}
        league = {'code': 'NCBA', 'name': 'NCBA'}
        
        cache = MagicMock()
        cache.get_team_by_code.return_value = team
        
        with patch.object(TeamPlayerCache, '__new__', return_value=cache):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_team_in_season', return_value=False):
                    await stats_cog.team_stats(mock_interaction, 'PANDAS')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert 'does not play in' in call_args
    
    asyncio.run(run_test())


def test_team_average_success(stats_cog, mock_interaction):
    """Test /team-average command with valid team and season."""
    import asyncio
    
    async def run_test():
        team = {'id': 'team-1', 'name': 'Pandas', 'code': 'PANDAS', 'division_id': 'div-1', 'league_type': 'NBL', 'active_season_id': 'season-1'}
        season = {'id': 'season-1', 'name': 'S10', 'division_id': 'div-1'}
        league = {'code': 'NBL', 'name': 'NBL Prime'}
        averages = {'ppg': 42.3, 'apg': 8.5, 'games_played': 5}
        
        cache = MagicMock()
        cache.get_team_by_code.return_value = team
        
        with patch.object(TeamPlayerCache, '__new__', return_value=cache):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_team_in_season', return_value=True):
                    with patch.object(stats_cog, '_resolve_league_for_team', return_value=league):
                        with patch.object(queries, 'get_team_averages', new_callable=AsyncMock, return_value=averages):
                            await stats_cog.team_average(mock_interaction, 'PANDAS')
                            
                            assert mock_interaction.followup.send.called
                            call_kwargs = mock_interaction.followup.send.call_args[1]
                            assert 'embed' in call_kwargs
    
    asyncio.run(run_test())


# =====================================================================
# Tests for player-history command
# =====================================================================

def test_player_history_player_not_in_season(stats_cog, mock_interaction):
    """Test /player-history rejects player not in season."""
    import asyncio
    
    async def run_test():
        player = {'id': 'player-1', 'display_name': 'Ace'}
        season = {'id': 'season-2', 'name': 'S2', 'division_id': 'div-2'}
        league = {'code': 'NCBA', 'name': 'NCBA'}
        
        with patch.object(stats_cog, '_resolve_player', return_value=player):
            with patch.object(stats_cog, '_resolve_season_context', return_value=(season, league)):
                with patch.object(stats_cog, '_validate_player_in_season', return_value=False):
                    await stats_cog.player_history(mock_interaction, 'Ace')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert 'not on any roster' in call_args
    
    asyncio.run(run_test())


# =====================================================================
# Tests for compare command
# =====================================================================

def test_compare_player_a_not_in_season(stats_cog, mock_interaction):
    """Test /compare rejects player A not in season."""
    import asyncio
    
    async def run_test():
        player_a = {'id': 'player-1', 'display_name': 'Ace'}
        player_b = {'id': 'player-2', 'display_name': 'Bot'}
        season = {'id': 'season-1', 'name': 'S10', 'division_id': 'div-1'}
        league = {'code': 'NBL', 'name': 'NBL Prime'}
        
        with patch.object(stats_cog, '_find_season', return_value=(season, league)):
            with patch.object(stats_cog, '_resolve_player', side_effect=[player_a, player_b]):
                with patch.object(stats_cog, '_validate_player_in_season', side_effect=[False, True]):
                    await stats_cog.compare(mock_interaction, 'Ace', 'Bot')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert 'Ace' in call_args
                    assert 'not on any roster' in call_args
    
    asyncio.run(run_test())


def test_compare_player_b_not_in_season(stats_cog, mock_interaction):
    """Test /compare rejects player B not in season."""
    import asyncio
    
    async def run_test():
        player_a = {'id': 'player-1', 'display_name': 'Ace'}
        player_b = {'id': 'player-2', 'display_name': 'Bot'}
        season = {'id': 'season-1', 'name': 'S10', 'division_id': 'div-1'}
        league = {'code': 'NBL', 'name': 'NBL Prime'}
        
        with patch.object(stats_cog, '_find_season', return_value=(season, league)):
            with patch.object(stats_cog, '_resolve_player', side_effect=[player_a, player_b]):
                with patch.object(stats_cog, '_validate_player_in_season', side_effect=[True, False]):
                    await stats_cog.compare(mock_interaction, 'Ace', 'Bot')
                    
                    call_args = mock_interaction.followup.send.call_args[0][0]
                    assert 'Bot' in call_args
                    assert 'not on any roster' in call_args
    
    asyncio.run(run_test())


# =====================================================================
# Tests for league display helper
# =====================================================================

def test_format_league_display_with_code_and_name():
    """Test league formatting with both code and name."""
    from cogs.stats.stats_commands import StatsCog
    
    with patch('cogs.stats.stats_commands.get_supabase', return_value=MagicMock()):
        cog = StatsCog(AsyncMock())
        league = {'code': 'NBL', 'name': 'NBL Prime'}
        
        result = cog._format_league_display(league)
        assert 'NBL Prime' in result
        assert 'NBL' in result


def test_format_league_display_code_only():
    """Test league formatting with code only."""
    from cogs.stats.stats_commands import StatsCog
    
    with patch('cogs.stats.stats_commands.get_supabase', return_value=MagicMock()):
        cog = StatsCog(AsyncMock())
        league = {'code': 'NBL'}
        
        result = cog._format_league_display(league)
        assert 'NBL' in result


def test_format_league_display_none():
    """Test league formatting with None."""
    from cogs.stats.stats_commands import StatsCog
    
    with patch('cogs.stats.stats_commands.get_supabase', return_value=MagicMock()):
        cog = StatsCog(AsyncMock())
        
        result = cog._format_league_display(None)
        assert result == 'Unknown'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
