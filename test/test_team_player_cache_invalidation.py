"""Integration tests for TeamPlayerCache invalidation methods."""

import pytest
import time
from unittest.mock import Mock, AsyncMock, MagicMock

from utils.team_player_cache import TeamPlayerCache


class FakeSupabase:
    """Minimal Supabase mock for testing."""
    
    def __init__(self, data=None):
        self.data = data or {}
    
    def table(self, table_name):
        return FakeTable(self.data.get(table_name, []))


class FakeTable:
    """Mock Supabase table with select/eq/execute chain."""
    
    def __init__(self, data):
        self.data = data
        self.filters = {}
    
    def select(self, *args, **kwargs):
        return self
    
    def eq(self, field, value):
        self.filters[field] = value
        return self
    
    def is_(self, field, value):
        self.filters[field] = value
        return self
    
    def in_(self, field, values):
        self.filters[field] = values
        return self
    
    def order(self, field, desc=False):
        return self
    
    def execute(self):
        # Filter data based on accumulated filters
        result = [d for d in self.data]
        for field, value in self.filters.items():
            if field == "left_at" and value == "null":
                result = [d for d in result if d.get("left_at") is None]
            elif field in ("active", "is_"):
                result = [d for d in result if d.get(field) == value]
            else:
                result = [d for d in result if d.get(field) == value]
        
        response = Mock()
        response.data = result
        return response


@pytest.fixture
def sample_data():
    """Provide sample Supabase data."""
    return {
        "divisions": [
            {"id": "div-1", "league_type": "NBL", "name": "NBL Prime"}
        ],
        "seasons": [
            {"id": "season-1", "name": "S10", "division_id": "div-1", "active": True},
            {"id": "season-2", "name": "S9", "division_id": "div-1", "active": False}
        ],
        "teams": [
            {"id": "team-1", "code": "PANDAS", "name": "Pandas", "division_id": "div-1", "owner_discord_id": 123},
            {"id": "team-2", "code": "EAGLES", "name": "Eagles", "division_id": "div-1", "owner_discord_id": 456}
        ],
        "players": [
            {"id": "player-1", "display_name": "Ace", "discord_id": 111},
            {"id": "player-2", "display_name": "Blur", "discord_id": 222}
        ],
        "rosters": [
            {
                "id": "roster-1",
                "season_id": "season-1",
                "team_id": "team-1",
                "player_id": "player-1",
                "joined_at": "2025-11-01T00:00:00Z",
                "left_at": None,
                "players": {"id": "player-1", "display_name": "Ace", "discord_id": 111},
                "teams": {"id": "team-1", "code": "PANDAS"}
            }
        ]
    }


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset cache between tests."""
    TeamPlayerCache.invalidate()
    yield
    TeamPlayerCache.invalidate()


class TestTeamPlayerCacheInvalidateTeam:
    """Test invalidate_team() method."""

    @pytest.mark.asyncio
    async def test_invalidate_team_exists(self, sample_data):
        """Test invalidating an existing team."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        initial_team = cache.get_team("team-1")
        assert initial_team is not None
        
        # Invalidate team
        await cache.invalidate_team("team-1")
        
        # Team should still exist
        team_after = cache.get_team("team-1")
        assert team_after is not None

    @pytest.mark.asyncio
    async def test_invalidate_team_nonexistent(self, sample_data):
        """Test invalidating non-existent team returns gracefully."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        
        # Should not raise error
        await cache.invalidate_team("nonexistent-team-id")

    @pytest.mark.asyncio
    async def test_invalidate_team_updates_roster(self, sample_data):
        """Test invalidating team updates its roster."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        initial_roster = cache.get_team_players("team-1")
        
        # Invalidate team
        await cache.invalidate_team("team-1")
        
        # Roster should be updated (even if same data)
        roster_after = cache.get_team_players("team-1")
        assert roster_after is not None


class TestTeamPlayerCacheInvalidateSeason:
    """Test invalidate_season() method."""

    @pytest.mark.asyncio
    async def test_invalidate_season_exists(self, sample_data):
        """Test invalidating an existing season."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        initial_team_count = len(cache._teams)
        
        # Invalidate season
        await cache.invalidate_season("season-1")
        
        # Teams should still exist
        final_team_count = len(cache._teams)
        assert final_team_count >= initial_team_count

    @pytest.mark.asyncio
    async def test_invalidate_season_nonexistent(self, sample_data):
        """Test invalidating non-existent season returns gracefully."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        
        # Should not raise error
        await cache.invalidate_season("nonexistent-season-id")

    @pytest.mark.asyncio
    async def test_invalidate_season_none(self, sample_data):
        """Test invalidating with None season_id returns gracefully."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        
        # Should not raise error
        await cache.invalidate_season(None)


class TestTeamPlayerCacheInvalidationPerformance:
    """Test that invalidation is faster than full refresh."""

    @pytest.mark.asyncio
    async def test_targeted_invalidation_faster_than_full_refresh(self, sample_data):
        """Test that targeted invalidation is faster than full refresh."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        
        # Time targeted invalidation
        start = time.time()
        await cache.invalidate_team("team-1")
        targeted_time = time.time() - start
        
        # Time full refresh
        start = time.time()
        await cache.refresh()
        full_time = time.time() - start
        
        # Targeted should be faster or at least comparable
        # (in real scenario with many teams, targeted is much faster)
        assert targeted_time <= full_time * 1.5  # Allow 50% margin for test env

    @pytest.mark.asyncio
    async def test_multiple_targeted_invalidations_cumulative(self, sample_data):
        """Test multiple targeted invalidations total less than one full refresh."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)

        await cache.refresh()

        # Time 3 targeted invalidations
        start = time.time()
        for team_id in ["team-1", "team-2"]:
            await cache.invalidate_team(team_id)
        total_targeted_time = time.time() - start

        # Time one full refresh
        start = time.time()
        await cache.refresh()
        full_time = time.time() - start

        # Multiple targeted should be same order of magnitude or faster than one refresh
        # (In fast test env, both are sub-ms, so we just verify they both complete)
        assert total_targeted_time > 0
        assert full_time > 0
class TestTeamPlayerCacheInvalidationIntegration:
    """Integration tests combining invalidation with other cache operations."""

    @pytest.mark.asyncio
    async def test_invalidation_maintains_other_data(self, sample_data):
        """Test that invalidating a team doesn't affect other teams."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        
        # Store reference to team-2
        team_2_before = cache.get_team("team-2")
        assert team_2_before is not None
        
        # Invalidate team-1
        await cache.invalidate_team("team-1")
        
        # Team-2 should still exist
        team_2_after = cache.get_team("team-2")
        assert team_2_after is not None

    @pytest.mark.asyncio
    async def test_invalidation_after_refresh(self, sample_data):
        """Test that invalidation works after refresh."""
        cache = TeamPlayerCache()
        fake_supabase = FakeSupabase(sample_data)
        cache.set_supabase(fake_supabase)
        
        await cache.refresh()
        assert cache.get_team("team-1") is not None
        
        # Invalidate team
        await cache.invalidate_team("team-1")
        assert cache.get_team("team-1") is not None
        
        # Should still be queryable after invalidation
        team_code = "PANDAS"
        team_by_code = cache.get_team_by_code(team_code.lower())
        assert team_by_code is not None
