"""Rosters and history names come from the cache when they can, and never from whole-league reads."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cogs.transactions import history
from db import queries
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache


@pytest.fixture(autouse=True)
def caches():
    TeamPlayerCache.invalidate()
    LeagueCache.load_snapshot(seasons=[{"id": "s1", "number": 2, "name": "S2", "active": True},
                                       {"id": "s0", "number": 1, "name": "S1", "active": False}], divisions=[])
    ace = {"id": "p1", "display_name": "Ace", "discord_id": 11}
    zed = {"id": "p2", "display_name": "Zed", "discord_id": None}
    TeamPlayerCache._store_team({"id": "t1", "code": "pack", "name": "Pack"})
    TeamPlayerCache._store_team({"id": "t2", "code": "owls", "name": "Owls", "archived_at": "2026-01-01T00:00:00Z"})
    TeamPlayerCache._players = {"p1": ace, "p2": zed}
    TeamPlayerCache._team_players = {"t1": [ace, zed]}
    yield
    TeamPlayerCache.invalidate()
    LeagueCache.invalidate()


def _database(rows):
    supabase = MagicMock()
    chain = supabase.table.return_value.select.return_value
    chain.eq.return_value.eq.return_value.is_.return_value.execute.return_value = Mock(data=rows)
    return supabase


@pytest.mark.asyncio
async def test_active_season_roster_comes_from_the_cache_in_roster_row_shape():
    supabase = _database([])

    rows = await queries.get_team_roster(supabase, "s1", "t1")

    assert [(r["team_id"], r["player_id"], r["players"]["display_name"]) for r in rows] == [
        ("t1", "p1", "Ace"), ("t1", "p2", "Zed"),
    ]
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_a_past_season_asks_the_database_for_just_that_team():
    supabase = _database([{"player_id": "p9", "team_id": "t1", "players": {"display_name": "Old"}}])

    rows = await queries.get_team_roster(supabase, "s0", "t1")

    assert rows[0]["player_id"] == "p9"
    select = supabase.table.return_value.select.return_value
    select.eq.assert_called_once_with("season_id", "s0")
    select.eq.return_value.eq.assert_called_once_with("team_id", "t1")


@pytest.mark.asyncio
async def test_a_team_with_nobody_on_the_roster_returns_nothing():
    assert await queries.get_team_roster(_database([]), "s1", "t-empty") == []


@pytest.mark.asyncio
async def test_history_names_come_from_the_cache_for_just_the_mentioned_ids():
    supabase = MagicMock()
    transactions = [
        {"from_team_id": "t1", "to_team_id": "t2", "player_id": "p1"},
        {"from_team_id": None, "to_team_id": "t-unknown", "player_id": None},
    ]

    teams, players = await history.load_history_names(supabase, transactions)

    assert set(teams) == {"t1", "t2"}  # an archived team can still appear in old transactions
    assert set(players) == {"p1"}
    supabase.table.assert_not_called()


@pytest.mark.asyncio
async def test_history_names_fall_back_to_the_database_before_the_cache_is_loaded():
    TeamPlayerCache.invalidate()
    supabase = _database([])
    supabase.table.return_value.select.return_value.execute.return_value = Mock(data=[{"id": "p5", "display_name": "Db"}])

    with patch.object(history.queries, "get_all_teams", AsyncMock(return_value=[{"id": "t5", "name": "Db Team"}])):
        teams, players = await history.load_history_names(supabase, [{"player_id": "p5"}])

    assert set(teams) == {"t5"} and set(players) == {"p5"}
