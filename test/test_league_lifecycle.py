"""Tests for the season lifecycle service."""

from unittest.mock import AsyncMock, patch

import pytest

from db.league_repository import LeagueRuleError
from services import league
from services.league import (
    direct_movements,
    end_regular_season,
    finish_postseason,
    regular_season_readiness,
    start_next_season,
    start_regular_season,
    suggested_next_name,
)
from utils.league_cache import LeagueCache

D1_SETTINGS = {"boundary_below": {"direct_swaps": 1}, "championship": {"enabled": False}}


def load(phase="setup", assignments=None, d1_settings=None):
    season = {"id": "s1", "number": 1, "name": "Season 1", "active": True, "phase": phase}
    LeagueCache.load_snapshot(
        seasons=[season],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "settings": d1_settings or D1_SETTINGS},
            {"id": "d2", "code": "D2", "name": "Second", "tier": 2, "settings": {}},
        ],
        assignments={"s1": assignments if assignments is not None else [
            {"team_id": t, "division_id": d, "group_id": None}
            for t, d in (("a", "d1"), ("b", "d1"), ("c", "d1"), ("x", "d2"), ("y", "d2"), ("z", "d2"))
        ]},
    )
    return season


@pytest.fixture(autouse=True)
def cleanup():
    yield
    LeagueCache.invalidate()


def repo_mock():
    repo = AsyncMock()
    repo.get_standings.return_value = []
    repo.list_season_teams.return_value = []
    repo.list_regular_matches.return_value = []
    repo.list_brackets.return_value = []
    return repo


def test_readiness_blocks_small_divisions_and_warns_about_unassigned_teams():
    load(assignments=[{"team_id": "a", "division_id": "d1", "group_id": None}])
    readiness = regular_season_readiness("s1", ["a", "b"])
    assert not readiness.ok
    assert any("D1 needs at least 2 teams" in p for p in readiness.problems)
    assert any("D2 needs at least 2 teams" in p for p in readiness.problems)
    assert readiness.warnings == ["1 team(s) have no division and will sit this season out."]


def test_readiness_reports_zone_overlaps():
    load(d1_settings={"championship": {"enabled": True, "teams": 4}})
    readiness = regular_season_readiness("s1", ["a", "b", "c", "x", "y", "z"])
    assert any("championship playoff needs 4" in p for p in readiness.problems)


@pytest.mark.asyncio
async def test_start_regular_season_moves_phase_and_builds_standings():
    season = load()
    repo = repo_mock()
    with patch.object(LeagueCache, "load_season_teams", AsyncMock()):
        await start_regular_season(repo, season, ["a", "b", "c", "x", "y", "z"])
    repo.set_season_phase.assert_awaited_once_with("s1", "regular")
    assert repo.replace_standings.await_count == 2


@pytest.mark.asyncio
async def test_start_regular_season_refuses_when_not_ready():
    season = load(assignments=[])
    repo = repo_mock()
    with patch.object(LeagueCache, "load_season_teams", AsyncMock()), pytest.raises(LeagueRuleError):
        await start_regular_season(repo, season, ["a"])
    repo.set_season_phase.assert_not_awaited()


@pytest.mark.asyncio
async def test_steps_check_the_phase():
    season = load(phase="regular")
    with pytest.raises(LeagueRuleError):
        await start_regular_season(repo_mock(), season, [])
    with pytest.raises(LeagueRuleError):
        await start_next_season(repo_mock(), season, "S2")


@pytest.mark.asyncio
async def test_end_regular_season_sends_the_plan():
    season = load(phase="regular")
    repo = repo_mock()
    repo.create_postseason.return_value = 0
    builder = lambda season_id, standings: {"brackets": [], "seen": sorted(standings)}  # noqa: E731
    await end_regular_season(repo, season, plan_builder=builder)
    season_id, plan = repo.create_postseason.await_args.args
    assert season_id == "s1" and plan["seen"] == ["d1", "d2"]


def test_direct_movements_follow_zones_and_adjacent_tiers():
    load(phase="postseason")
    standings = {
        "d1": [{"team_id": "a", "zone": None}, {"team_id": "c", "zone": "relegated"}],
        "d2": [{"team_id": "x", "zone": "promoted"}, {"team_id": "z", "zone": "relegated"}],  # no tier below D2
    }
    moves = [(m.team_id, m.to_division_id, m.reason) for m in direct_movements(standings)]
    assert moves == [("c", "d2", "relegated"), ("x", "d1", "promoted")]


@pytest.mark.asyncio
async def test_finish_postseason_records_champion_and_moves():
    season = load(phase="postseason")
    repo = repo_mock()
    repo.get_standings.side_effect = lambda season_id, division_id: {
        "d1": [{"team_id": "a", "rank": 1, "zone": None}, {"team_id": "c", "rank": 2, "zone": "relegated"}],
        "d2": [{"team_id": "x", "rank": 1, "zone": "promoted"}],
    }[division_id]
    outcome = await finish_postseason(repo, season)
    assert outcome.champion_team_id == "a"
    season_id, champion, moves = repo.finalize_season.await_args.args
    assert champion == "a"
    assert {m["team_id"] for m in moves} == {"c", "x"}


@pytest.mark.asyncio
async def test_finish_postseason_waits_for_brackets():
    season = load(phase="postseason")
    repo = repo_mock()
    repo.list_brackets.return_value = [{"id": "b1", "status": "active"}]
    with patch.object(league, "season_outcome", AsyncMock(return_value=league.SeasonOutcome(None, [], 1))):
        with pytest.raises(LeagueRuleError):
            await finish_postseason(repo, season)
    repo.finalize_season.assert_not_awaited()


def test_suggested_next_name():
    assert suggested_next_name({"name": "Season 3", "number": 3}) == "Season 4"
    assert suggested_next_name({"name": "Spring Cup", "number": 2}) == "Season 3"
