"""Team lookups by owner and code come from the team cache once it is loaded."""

from unittest.mock import MagicMock, Mock

import pytest

from db import queries
from utils.team_player_cache import TeamPlayerCache


@pytest.fixture(autouse=True)
def clean_cache():
    TeamPlayerCache.invalidate()
    yield
    TeamPlayerCache.invalidate()


def _load(*teams):
    for team in teams:
        TeamPlayerCache._store_team(team)


def _database(rows):
    supabase = MagicMock()
    chain = supabase.table.return_value.select.return_value
    chain.eq.return_value.limit.return_value.execute.return_value = Mock(data=rows)
    chain.eq.return_value.is_.return_value.limit.return_value.execute.return_value = Mock(data=rows)
    return supabase


@pytest.mark.asyncio
async def test_owner_lookup_uses_the_cache_when_loaded():
    _load({"id": "t1", "code": "pack", "name": "Pack", "owner_discord_id": 7})
    supabase = _database([])

    team = await queries.get_team_by_owner(supabase, 7)

    assert team["id"] == "t1"
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_a_user_who_owns_nothing_is_not_looked_up_in_the_database():
    _load({"id": "t1", "code": "pack", "name": "Pack", "owner_discord_id": 7})
    supabase = _database([{"id": "x"}])

    assert await queries.get_team_by_owner(supabase, 99) is None
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_an_archived_team_has_no_owner():
    _load({"id": "t1", "code": "pack", "name": "Pack", "owner_discord_id": 7, "archived_at": "2026-01-01T00:00:00Z"})

    assert await queries.get_team_by_owner(_database([]), 7) is None


@pytest.mark.asyncio
async def test_code_lookup_is_case_insensitive_and_cached():
    _load({"id": "t1", "code": "pack", "name": "Pack"})
    supabase = _database([])

    assert (await queries.get_team_by_code(supabase, " PACK "))["id"] == "t1"
    assert await queries.get_team_by_code(supabase, "nope") is None
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_callers_get_a_copy_they_cannot_use_to_change_the_cache():
    _load({"id": "t1", "code": "pack", "name": "Pack", "owner_discord_id": 7})

    team = await queries.get_team_by_owner(_database([]), 7)
    team["owner_discord_id"] = 999

    assert TeamPlayerCache.get_team("t1")["owner_discord_id"] == 7


@pytest.mark.asyncio
async def test_before_the_cache_is_loaded_the_database_answers():
    supabase = _database([{"id": "t9", "code": "owl", "name": "Owls", "owner_discord_id": 5}])

    assert (await queries.get_team_by_owner(supabase, 5))["id"] == "t9"
    assert (await queries.get_team_by_code(supabase, "owl"))["id"] == "t9"
