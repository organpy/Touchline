"""Stats commands learn a player's team and division for a season from a single roster lookup."""

from unittest.mock import AsyncMock, Mock

import pytest

from cogs.stats.base import StatsCogBase
from utils.league_cache import LeagueCache


@pytest.fixture(autouse=True)
def league():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True}],
        divisions=[{"id": "d1", "code": "D1", "name": "Premier", "tier": 1}],
        assignments={"s1": [{"team_id": "t1", "division_id": "d1", "group_id": None}]},
    )
    yield
    LeagueCache.invalidate()


def _cog(team_id):
    cog = StatsCogBase.__new__(StatsCogBase)
    cog._player_team_in_season = AsyncMock(return_value=team_id)
    return cog


@pytest.mark.asyncio
async def test_player_on_a_roster_is_in_the_season_with_their_division():
    cog = _cog("t1")

    in_season, division = await cog._player_in_season({"id": "p1"}, {"id": "s1"})

    assert in_season is True
    assert division["code"] == "D1"
    cog._player_team_in_season.assert_awaited_once_with("p1", "s1")


@pytest.mark.asyncio
async def test_player_without_a_roster_spot_is_not_in_the_season():
    cog = _cog(None)

    assert await cog._player_in_season({"id": "p1"}, {"id": "s1"}) == (False, None)
    cog._player_team_in_season.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("player, season", [(None, {"id": "s1"}), ({"id": "p1"}, None), ({}, {"id": "s1"}), ({"id": "p1"}, {})])
async def test_missing_player_or_season_needs_no_lookup(player, season):
    cog = _cog("t1")

    assert await cog._player_in_season(player, season) == (False, None)
    cog._player_team_in_season.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_player_on_an_unassigned_team_is_in_the_season_without_a_division():
    cog = _cog("t-unassigned")

    in_season, division = await cog._player_in_season({"id": "p1"}, {"id": "s1"})

    assert in_season is True and division is None
