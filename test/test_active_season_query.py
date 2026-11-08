"""get_active_season is answered from the league cache; the database is only the fallback."""

from unittest.mock import MagicMock, Mock

import pytest

from db import queries
from utils.league_cache import LeagueCache


@pytest.fixture(autouse=True)
def clean_cache():
    LeagueCache.invalidate()
    yield
    LeagueCache.invalidate()


def _database(rows):
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = Mock(data=rows)
    return supabase


@pytest.mark.asyncio
async def test_cached_active_season_needs_no_database_call():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True, "phase": "regular"}],
        divisions=[{"id": "d1", "code": "D1", "name": "Premier", "tier": 1}],
    )
    supabase = _database([])

    season = await queries.get_active_season(supabase)

    assert season["id"] == "s1"
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_the_division_is_attached_to_a_copy_not_the_cache():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True}],
        divisions=[{"id": "d1", "code": "D1", "name": "Premier", "tier": 1}],
    )

    season = await queries.get_active_season(_database([]), division_id="d1")
    season["name"] = "changed by a caller"

    assert season["league_type"] == "D1"
    assert LeagueCache.active_season()["name"] == "S1"
    assert "division_id" not in LeagueCache.active_season()


@pytest.mark.asyncio
async def test_empty_cache_falls_back_to_the_database():
    supabase = _database([{"id": "s9", "name": "S9", "active": True}])

    season = await queries.get_active_season(supabase)

    assert season["id"] == "s9"
    supabase.table.assert_called_once_with("seasons")


@pytest.mark.asyncio
async def test_database_failure_without_a_cache_returns_none():
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = ConnectionError("down")

    assert await queries.get_active_season(supabase) is None
