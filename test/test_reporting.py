"""Tests for the shared game-reporting rules (services.reporting)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.league_repository import LeagueRuleError
from services import reporting
from services.reporting import MatchContext, record_match, resolve_match_context, void_match
from utils.league_cache import LeagueCache


def load(phase="regular", games=2):
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True, "phase": phase}],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "settings": {"games_per_matchup": games}},
            {"id": "d2", "code": "D2", "name": "Second", "tier": 2, "settings": {}},
        ],
        assignments={"s1": [
            {"team_id": "a", "division_id": "d1", "group_id": None},
            {"team_id": "b", "division_id": "d1", "group_id": None},
            {"team_id": "x", "division_id": "d2", "group_id": None},
        ]},
    )


@pytest.fixture(autouse=True)
def cleanup():
    yield
    LeagueCache.invalidate()


def entry(t1="a", t2="b", s1=80, s2=70):
    match = MagicMock()
    match.team1 = SimpleNamespace(team_id=t1, score=s1)
    match.team2 = SimpleNamespace(team_id=t2, score=s2)
    match.to_supabase_match.side_effect = lambda season_id, division_id: {
        "season_id": season_id, "division_id": division_id, "team1_id": t1, "team2_id": t2,
        "team1_score": s1, "team2_score": s2, "status": "final",
    }
    return match


async def test_regular_game_counts_in_the_shared_division():
    load()
    played = [{"team1_id": "b", "team2_id": "a", "team1_score": 60, "team2_score": 66}]
    with patch.object(reporting.queries, "get_matches_between_teams", AsyncMock(return_value=played)):
        context = await resolve_match_context(MagicMock(), "a", "b")
    assert (context.stage, context.division_id, context.games_limit) == ("regular", "d1", 2)
    assert not context.is_playoff and not context.at_limit
    assert context.match_fields() == {"division_id": "d1"}
    lines = context.summary_lines("a", "A", "B")
    assert "Limit: **2** · Played: **1**" in lines[1]
    assert lines[2] == "• 66 - 60"   # scores shown from team a's side


async def test_regular_game_between_divisions_is_refused():
    load()
    with pytest.raises(LeagueRuleError) as err:
        await resolve_match_context(MagicMock(), "a", "x")
    assert err.value.code == "MATCH_DIVISION_MISMATCH"


async def test_unassigned_team_is_refused():
    load()
    with pytest.raises(LeagueRuleError) as err:
        await resolve_match_context(MagicMock(), "a", "nobody")
    assert err.value.code == "MATCH_TEAM_NOT_IN_SEASON"


async def test_games_limit_blocks_more_games():
    load(games=1)
    with patch.object(reporting.queries, "get_matches_between_teams", AsyncMock(return_value=[{"team1_id": "a"}])):
        context = await resolve_match_context(MagicMock(), "a", "b")
    assert context.at_limit
    with pytest.raises(LeagueRuleError) as err:
        context.ensure_open()
    assert err.value.code == "MATCH_LIMIT"


@pytest.mark.parametrize("phase", ["setup", "offseason", "completed"])
async def test_no_games_outside_regular_season_and_postseason(phase):
    load(phase=phase)
    with pytest.raises(LeagueRuleError) as err:
        await resolve_match_context(MagicMock(), "a", "b")
    assert err.value.code == "MATCH_WRONG_PHASE"


def series_repo(series):
    repo = AsyncMock()
    repo.find_ready_series.return_value = series
    repo.list_series_matches.return_value = [{"status": "final", "team1_id": "a", "team1_score": 1, "team2_score": 0},
                                             {"status": "void"}]
    return repo


async def test_postseason_game_attaches_to_the_open_series():
    load(phase="postseason")
    series = {"id": "ser1", "team1_id": "b", "team2_id": "a", "team1_wins": 0, "team2_wins": 1, "best_of": 3,
              "brackets": {"kind": "championship", "division_id": "d1"}}
    with patch.object(reporting, "LeagueRepository", return_value=series_repo(series)):
        context = await resolve_match_context(MagicMock(), "a", "b")
    assert context.is_playoff and context.stage == "championship"
    assert len(context.played) == 1          # voided games don't count
    assert not context.at_limit              # series have no games limit
    assert context.match_fields() == {"series_id": "ser1", "division_id": "d1"}
    assert "Series: **1–0** · best of 3 (first to 2)" in context.summary_lines("a", "A", "B")[1]


async def test_cross_division_playoff_has_no_division():
    load(phase="postseason")
    series = {"id": "ser2", "team1_id": "b", "team2_id": "x",
              "brackets": {"kind": "promo_rel", "division_id": "d1", "lower_division_id": "d2"}}
    with patch.object(reporting, "LeagueRepository", return_value=series_repo(series)):
        context = await resolve_match_context(MagicMock(), "b", "x")
    assert context.division_id is None


async def test_postseason_without_a_series_is_refused():
    load(phase="postseason")
    with patch.object(reporting, "LeagueRepository", return_value=series_repo(None)):
        with pytest.raises(LeagueRuleError) as err:
            await resolve_match_context(MagicMock(), "a", "b")
    assert err.value.code == "MATCH_NO_SERIES"


def recording_repo(match):
    repo = series_repo(None)
    repo.record_match.return_value = match
    return repo


async def test_record_regular_match_saves_with_stats_and_refreshes_standings():
    load()
    supabase = MagicMock()
    match_entry = entry()
    repo = recording_repo({"id": "m1", "division_id": "d1"})
    with patch.object(reporting.queries, "get_matches_between_teams", AsyncMock(return_value=[])), \
         patch.object(reporting, "LeagueRepository", return_value=repo), \
         patch.object(reporting, "refresh_after_match", AsyncMock()) as refresh:
        match, context = await record_match(supabase, match_entry, recorded_by=42)
    assert match["id"] == "m1" and context.stage == "regular"
    payload, stats, report_id = repo.record_match.await_args.args
    assert payload["division_id"] == "d1" and "series_id" not in payload and payload["recorded_by"] == 42
    assert stats == match_entry.to_supabase_player_stats.return_value
    assert report_id == match_entry.report_id
    refresh.assert_awaited_once_with(supabase, "s1", "d1")


async def test_record_playoff_match_sets_series_and_skips_standings():
    load(phase="postseason")
    series = {"id": "ser1", "team1_id": "a", "team2_id": "b", "brackets": {"kind": "promotion", "division_id": "d1"}}
    repo = recording_repo({"id": "m2"})
    repo.find_ready_series.return_value = series
    with patch.object(reporting, "LeagueRepository", return_value=repo), \
         patch.object(reporting, "refresh_after_match", AsyncMock()) as refresh:
        _, context = await record_match(MagicMock(), entry())
    payload = repo.record_match.await_args.args[0]
    assert payload["series_id"] == "ser1"
    assert context.stage == "promotion"
    refresh.assert_not_awaited()


async def test_record_match_refuses_when_the_limit_is_reached():
    load(games=1)
    repo = recording_repo({"id": "m3"})
    with patch.object(reporting.queries, "get_matches_between_teams", AsyncMock(return_value=[{}])), \
         patch.object(reporting, "LeagueRepository", return_value=repo):
        with pytest.raises(LeagueRuleError):
            await record_match(MagicMock(), entry())
    repo.record_match.assert_not_awaited()


async def test_record_match_reports_an_unsaved_game():
    load()
    repo = recording_repo(None)
    with patch.object(reporting.queries, "get_matches_between_teams", AsyncMock(return_value=[])), \
         patch.object(reporting, "LeagueRepository", return_value=repo):
        with pytest.raises(LeagueRuleError) as err:
            await record_match(MagicMock(), entry())
    assert err.value.code == "MATCH_INVALID"


async def test_void_regular_match_refreshes_standings():
    repo = AsyncMock()
    with patch.object(reporting, "LeagueRepository", return_value=repo), \
         patch.object(reporting, "refresh_after_match", AsyncMock()) as refresh:
        await void_match("sb", {"id": "m1", "stage": "regular", "season_id": "s1", "division_id": "d1"}, 42)
    repo.void_match.assert_awaited_once_with("m1", 42)
    refresh.assert_awaited_once_with("sb", "s1", "d1")


async def test_void_playoff_match_leaves_standings_alone():
    repo = AsyncMock()
    with patch.object(reporting, "LeagueRepository", return_value=repo), \
         patch.object(reporting, "refresh_after_match", AsyncMock()) as refresh:
        await void_match("sb", {"id": "m2", "stage": "championship"}, 42)
    refresh.assert_not_awaited()


def test_context_without_limit_is_never_full():
    context = MatchContext(season={"id": "s1"}, stage="regular", division_id="d1", games_limit=None, played=[{}] * 9)
    assert not context.at_limit


class _FailingQuery:
    """Any query chain whose execute() fails like a dropped connection."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: self

    def execute(self):
        raise ConnectionError("database unreachable")


async def test_games_played_read_failure_is_not_treated_as_no_games():
    supabase = MagicMock()
    supabase.table.return_value = _FailingQuery()

    with pytest.raises(ConnectionError):
        await reporting.queries.get_matches_between_teams(supabase, "s1", "a", "b")


async def test_record_match_fails_closed_when_games_played_cannot_be_read():
    load()
    repo = recording_repo({"id": "m4"})
    failing = AsyncMock(side_effect=ConnectionError("database unreachable"))
    with patch.object(reporting.queries, "get_matches_between_teams", failing), \
         patch.object(reporting, "LeagueRepository", return_value=repo):
        with pytest.raises(ConnectionError):
            await record_match(MagicMock(), entry())
    repo.record_match.assert_not_awaited()
