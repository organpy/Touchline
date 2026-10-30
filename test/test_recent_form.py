"""Recent results for the standings image come from one list of games."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.stats.commands import standings as standings_command
from services.standings import recent_form


def game(day, t1, t2, s1, s2):
    return {"team1_id": t1, "team2_id": t2, "team1_score": s1, "team2_score": s2, "played_at": f"2026-11-{day:02d}T12:00:00+00:00"}


def test_results_are_newest_first_and_padded():
    matches = [game(1, "a", "b", 5, 3), game(2, "b", "a", 9, 1), game(3, "a", "c", 4, 2)]

    form = recent_form(matches, ["a", "b", "c", "d"])

    assert form["a"] == ["W", "L", "W", "N", "N"]  # day 3 win, day 2 loss, day 1 win
    assert form["b"] == ["W", "L", "N", "N", "N"]
    assert form["c"] == ["L", "N", "N", "N", "N"]
    assert form["d"] == ["N"] * 5


def test_only_the_last_five_count():
    matches = [game(day, "a", "b", 5, 3) for day in range(1, 9)] + [game(9, "a", "b", 1, 2)]

    assert recent_form(matches, ["a"])["a"] == ["L", "W", "W", "W", "W"]


def test_other_teams_games_do_not_leak_in():
    matches = [game(1, "x", "y", 5, 3)]

    assert recent_form(matches, ["a"]) == {"a": ["N"] * 5}


def test_games_without_a_score_are_skipped():
    matches = [{"team1_id": "a", "team2_id": "b", "team1_score": None, "team2_score": None, "played_at": "2026-11-01"}]

    assert recent_form(matches, ["a"])["a"] == ["N"] * 5


@pytest.mark.asyncio
async def test_standings_load_every_teams_form_with_one_query():
    cog = standings_command.StandingsCog.__new__(standings_command.StandingsCog)
    cog.supabase = MagicMock()
    repo = MagicMock(list_regular_matches=AsyncMock(return_value=[game(1, "a", "b", 5, 3)]))
    rows = [{"team_id": "a"}, {"team_id": "b"}, {"team_id": "c"}, {"team_id": None}]

    with patch.object(standings_command, "LeagueRepository", return_value=repo):
        form = await cog._recent_results("s1", rows, "d1")

    repo.list_regular_matches.assert_awaited_once_with("s1", "d1")
    assert form["a"][0] == "W" and form["b"][0] == "L" and form["c"] == ["N"] * 5
