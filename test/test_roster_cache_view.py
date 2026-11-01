"""The score reporting roster cache is a live view of TeamPlayerCache and never queries the database."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.score.roster_cache import CachedPlayer, RosterCache
from utils.team_player_cache import TeamPlayerCache


@pytest.fixture(autouse=True)
def teams():
    TeamPlayerCache.invalidate()
    pack = {"id": "t1", "code": "pack", "name": "Pack", "emoji": "🐺", "division_id": "d1"}
    owls = {"id": "t2", "code": "owls", "name": "Owls", "division_id": "d1"}
    herd = {"id": "t3", "code": "herd", "name": "Herd", "division_id": "d2"}
    gone = {"id": "t4", "code": "gone", "name": "Gone", "division_id": "d1", "archived_at": "2026-01-01T00:00:00Z"}
    bench = {"id": "t5", "code": "bench", "name": "Bench"}  # no division this season
    for team in (pack, owls, herd, gone, bench):
        TeamPlayerCache._store_team(team)
    ace = {"id": "p1", "display_name": "Ace", "discord_id": 11, "current_team_id": "t1"}
    zed = {"id": "p2", "display_name": "Zed", "discord_id": None, "current_team_id": "t1"}
    free = {"id": "p3", "display_name": "Free Agent"}  # on no roster
    TeamPlayerCache._players = {p["id"]: p for p in (ace, zed, free)}
    TeamPlayerCache._team_players = {"t1": [ace, zed]}
    yield
    TeamPlayerCache.invalidate()


def test_team_comes_with_its_active_players():
    team = RosterCache().get_team_by_code("pack")

    assert (team.team_id, team.name, team.emoji, team.division_id) == ("t1", "Pack", "🐺", "d1")
    assert [p.display_name for p in team.active_players] == ["Ace", "Zed"]
    assert team.active_players[0] == CachedPlayer("p1", "Ace", 11)


def test_code_lookup_ignores_case_and_spaces_and_hides_archived_teams():
    cache = RosterCache()

    assert cache.get_team_by_code(" PACK ").code == "pack"
    assert cache.get_team_by_code("gone") is None
    assert cache.get_team_by_code("nope") is None
    assert cache.get_team_by_code("") is None


def test_same_league_needs_both_teams_in_one_division():
    cache = RosterCache()

    assert cache.teams_in_same_league("pack", "owls") is True
    assert cache.teams_in_same_league("pack", "herd") is False
    assert cache.teams_in_same_league("pack", "bench") is False  # no division this season
    assert cache.teams_in_same_league("pack", "missing") is False


def test_all_teams_leaves_out_archived_ones():
    assert {t.code for t in RosterCache().get_all_teams()} == {"pack", "owls", "herd", "bench"}


def test_players_are_found_by_team_and_by_name():
    cache = RosterCache()

    assert [p.display_name for p in cache.get_team_players("pack")] == ["Ace", "Zed"]
    assert cache.get_team_players("owls") == []
    assert [p.display_name for p in cache.search_players("pack", "ac")] == ["Ace"]
    assert cache.search_players("missing", "a") == []


def test_only_rostered_players_are_found_by_id():
    cache = RosterCache()

    assert cache.get_player_by_id("p1").display_name == "Ace"
    assert cache.get_player_by_id("p3") is None  # a free agent is not on any roster
    assert cache.get_player_by_id("nope") is None


def test_it_follows_the_team_cache_without_any_invalidation():
    cache = RosterCache()
    assert cache.get_team_by_code("owls").active_players == []

    TeamPlayerCache._team_players["t2"] = [{"id": "p4", "display_name": "New", "discord_id": None}]

    assert [p.display_name for p in cache.get_team_by_code("owls").active_players] == ["New"]


@pytest.mark.asyncio
async def test_loading_only_refreshes_when_the_team_cache_is_empty():
    cache = RosterCache()
    assert cache.loaded is True
    with patch.object(TeamPlayerCache, "refresh", AsyncMock()) as refresh:
        await cache.load_cache(Mock())
        refresh.assert_not_awaited()

        TeamPlayerCache.invalidate()
        assert cache.loaded is False
        await cache.load_cache("supabase")
        refresh.assert_awaited_once_with("supabase")
