"""LeagueCache reloads once for concurrent callers, never shows a half-loaded cache, and backs off after a failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from utils import league_cache
from utils.league_cache import LeagueCache

DATA = {
    "seasons": [{"id": "s1", "number": 1, "name": "S1", "active": True}],
    "divisions": [{"id": "d1", "code": "D1", "name": "Premier", "tier": 1}],
    "division_groups": [],
    "season_teams": [{"team_id": "t1", "division_id": "d1", "group_id": None}],
}


class FakeSupabase:
    def table(self, name):
        return SimpleNamespace(name=name, select=lambda *a: SimpleNamespace(
            name=name, order=lambda *a, **k: SimpleNamespace(name=name),
            eq=lambda *a: SimpleNamespace(name=name)))


@pytest.fixture(autouse=True)
def clean_cache():
    LeagueCache.invalidate()
    LeagueCache.set_supabase(FakeSupabase())
    yield
    LeagueCache.invalidate()
    LeagueCache.set_supabase(None)


def _fetch(calls, *, fail=False, on_season_teams=None):
    async def execute(query):
        calls.append(query.name)
        await asyncio.sleep(0)  # let other tasks run, like a real network call
        if fail:
            raise ConnectionError("database unreachable")
        if query.name == "season_teams" and on_season_teams:
            on_season_teams()
        return Mock(data=DATA[query.name])
    return execute


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_reload():
    calls = []
    with patch.object(league_cache, "execute_async", _fetch(calls)):
        await asyncio.gather(*(LeagueCache.ensure_ready() for _ in range(10)))

    assert sorted(calls) == ["division_groups", "divisions", "season_teams", "seasons"]
    assert LeagueCache.active_season()["id"] == "s1"


@pytest.mark.asyncio
async def test_a_fresh_cache_is_not_reloaded():
    calls = []
    with patch.object(league_cache, "execute_async", _fetch(calls)):
        await LeagueCache.ensure_ready()
        calls.clear()
        await LeagueCache.ensure_ready()

    assert calls == []


@pytest.mark.asyncio
async def test_an_explicit_refresh_reloads_even_when_fresh():
    calls = []
    with patch.object(league_cache, "execute_async", _fetch(calls)):
        await LeagueCache.ensure_ready()
        calls.clear()
        await LeagueCache.refresh()

    assert len(calls) == 4


@pytest.mark.asyncio
async def test_readers_keep_the_old_data_while_a_reload_is_running():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True}],
        divisions=[{"id": "d1", "code": "D1", "name": "Premier", "tier": 1}],
        assignments={"s1": [{"team_id": "t1", "division_id": "d1", "group_id": None}]},
    )
    seen = []
    calls = []
    fetch = _fetch(calls, on_season_teams=lambda: seen.append(LeagueCache.team_division("t1", "s1")))
    with patch.object(league_cache, "execute_async", fetch):
        await LeagueCache.refresh()

    assert seen and seen[0] is not None  # mid-reload, the previous assignments were still visible
    assert LeagueCache.team_division("t1", "s1")["id"] == "d1"


@pytest.mark.asyncio
async def test_a_failed_reload_keeps_the_old_data_and_backs_off():
    LeagueCache.load_snapshot(seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True}], divisions=[])
    LeagueCache._last_refresh = None  # make it stale
    calls = []
    with patch.object(league_cache, "execute_async", _fetch(calls, fail=True)):
        await LeagueCache.ensure_ready()
        attempts = len(calls)
        await LeagueCache.ensure_ready()  # within the back-off window: no new attempt

    assert attempts >= 1 and len(calls) == attempts
    assert LeagueCache.active_season()["id"] == "s1"
