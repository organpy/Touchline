"""Tests for division ranking, tiebreakers and zones."""

from unittest.mock import AsyncMock

import pytest

from services.division_settings import BoundaryConfig, ChampionshipConfig, DivisionSettings
from services.standings import (
    ZONE_CHAMPION_PLAYOFF,
    ZONE_PROMOTED,
    ZONE_PROMOTION_PLAYOFF,
    ZONE_RELEGATED,
    ZONE_RELEGATION_PLAYOFF,
    StandingRow,
    assign_zones,
    build_rows,
    championship_qualifiers,
    rank_rows,
    recompute_division,
    zone_conflicts,
)
from utils.league_cache import LeagueCache


def match(a, b, sa, sb):
    return {"team1_id": a, "team2_id": b, "team1_score": sa, "team2_score": sb}


def teams(*ids, groups=None):
    return [{"team_id": t, "name": t.upper(), "group_id": (groups or {}).get(t)} for t in ids]


def ranked(team_list, matches, settings=None):
    settings = settings or DivisionSettings()
    rows = build_rows(team_list, matches, settings)
    return rank_rows(rows.values(), matches, settings)


def order(rows):
    return [r.team_id for r in rows]


def test_points_scored_mode_ranks_by_total_points():
    rows = ranked(teams("a", "b", "c"), [match("a", "b", 10, 30), match("b", "c", 5, 40), match("a", "c", 50, 0)])
    # a: 60 pts, c: 40, b: 35
    assert order(rows) == ["a", "c", "b"]
    assert [r.rank for r in rows] == [1, 2, 3]
    assert rows[0].competition_points == 60


def test_win_points_mode_ranks_by_wins_times_points():
    settings = DivisionSettings(scoring_mode="win_points", points_per_win=3)
    rows = ranked(teams("a", "b", "c"), [match("a", "b", 1, 2), match("a", "c", 1, 2), match("b", "c", 9, 0)], settings)
    assert order(rows) == ["b", "c", "a"]
    assert rows[0].competition_points == 6


def test_point_difference_breaks_equal_points():
    settings = DivisionSettings(scoring_mode="win_points")
    rows = ranked(teams("a", "b", "c", "d"), [match("a", "c", 10, 0), match("b", "d", 3, 0)], settings)
    assert order(rows)[:2] == ["a", "b"]


def test_head_to_head_breaks_a_full_tie():
    settings = DivisionSettings(scoring_mode="win_points")
    # a and b finish level on points, diff and points for; b beat a.
    matches = [match("b", "a", 5, 4), match("a", "c", 6, 0), match("a", "c", 5, 0), match("b", "c", 10, 0), match("c", "b", 1, 0)]
    rows = ranked(teams("a", "b", "c"), matches, settings)
    a, b = (next(r for r in rows if r.team_id == t) for t in ("a", "b"))
    assert (a.competition_points, a.point_diff, a.points_for) == (b.competition_points, b.point_diff, b.points_for)
    assert order(rows)[:2] == ["b", "a"]
    assert "head-to-head" in rows[0].tiebreak


def test_name_is_the_last_tiebreaker_and_group_ranks_are_set():
    rows = ranked(teams("zeta", "alpha", "mid", groups={"zeta": "g1", "alpha": "g1", "mid": "g2"}), [])
    assert order(rows) == ["alpha", "mid", "zeta"]
    assert [r.group_rank for r in rows] == [1, 1, 2]


def rows_of(count, groups=None):
    result = []
    for i in range(count):
        row = StandingRow(team_id=f"t{i + 1}", rank=i + 1, group_id=(groups or {}).get(i))
        result.append(row)
    group_counts = {}
    for row in result:
        if row.group_id:
            group_counts[row.group_id] = group_counts.get(row.group_id, 0) + 1
            row.group_rank = group_counts[row.group_id]
    return result


def zones(rows):
    return [r.zone for r in rows]


def test_championship_zone_takes_the_top_teams():
    rows = rows_of(6)
    assign_zones(rows, championship=ChampionshipConfig(enabled=True, teams=4))
    assert zones(rows) == [ZONE_CHAMPION_PLAYOFF] * 4 + [None, None]


def test_championship_by_group_guarantees_each_group_its_spots():
    # Group B's best team is 5th overall but still qualifies.
    groups = {0: "A", 1: "A", 2: "A", 3: "A", 4: "B", 5: "B"}
    rows = rows_of(6, groups)
    qualified = championship_qualifiers(rows, ChampionshipConfig(enabled=True, teams=4, seeding="by_group"))
    assert [r.team_id for r in qualified] == ["t1", "t2", "t5", "t6"]


def test_promotion_and_relegation_zones_from_both_boundaries():
    rows = rows_of(10)
    above = BoundaryConfig(direct_swaps=1, playoff_swaps=1, mode="separate", upper_playoff_teams=2, lower_playoff_teams=4)
    below = BoundaryConfig(direct_swaps=2, playoff_swaps=1, mode="cross")
    assign_zones(rows, boundary_above=above, boundary_below=below)
    assert zones(rows) == [
        ZONE_PROMOTED,
        ZONE_PROMOTION_PLAYOFF, ZONE_PROMOTION_PLAYOFF, ZONE_PROMOTION_PLAYOFF, ZONE_PROMOTION_PLAYOFF,
        None, None,
        ZONE_RELEGATION_PLAYOFF,
        ZONE_RELEGATED, ZONE_RELEGATED,
    ]


def test_zones_never_overlap_when_space_runs_out():
    rows = rows_of(3)
    assign_zones(
        rows,
        championship=ChampionshipConfig(enabled=True, teams=2),
        boundary_below=BoundaryConfig(direct_swaps=2),
    )
    assert zones(rows) == [ZONE_CHAMPION_PLAYOFF, ZONE_CHAMPION_PLAYOFF, ZONE_RELEGATED]


def test_zone_conflicts_report_overlaps():
    below = BoundaryConfig(direct_swaps=2, playoff_swaps=1, mode="separate", upper_playoff_teams=2, lower_playoff_teams=4)
    assert zone_conflicts(8, championship=ChampionshipConfig(enabled=True, teams=4), boundary_below=below) == []
    errors = zone_conflicts(5, division_label="D1", championship=ChampionshipConfig(enabled=True, teams=4), boundary_below=below)
    assert any("overlaps the relegation places" in e for e in errors)
    assert zone_conflicts(3, championship=ChampionshipConfig(enabled=True, teams=4))


@pytest.fixture
def league():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True, "phase": "regular"}],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1,
             "settings": {"boundary_below": {"direct_swaps": 1}, "championship": {"enabled": True, "teams": 2}}},
            {"id": "d2", "code": "D2", "name": "Second", "tier": 2, "settings": {}},
        ],
    )
    yield
    LeagueCache.invalidate()


@pytest.mark.asyncio
async def test_recompute_division_writes_ranked_rows_with_zones(league):
    repo = AsyncMock()
    repo.get_standings.return_value = []
    repo.list_season_teams.return_value = [
        {"team_id": t, "group_id": None, "teams": {"name": t.upper()}} for t in ("a", "b", "c")
    ]
    repo.list_regular_matches.return_value = [match("a", "b", 30, 10), match("b", "c", 20, 5)]

    await recompute_division(repo, "s1", "d1")

    season_id, division_id, rows = repo.replace_standings.await_args.args
    assert (season_id, division_id) == ("s1", "d1")
    assert [(r["team_id"], r["rank"], r["zone"]) for r in rows] == [
        ("a", 1, ZONE_CHAMPION_PLAYOFF), ("b", 2, ZONE_CHAMPION_PLAYOFF), ("c", 3, ZONE_RELEGATED),
    ]
    assert rows[0]["point_diff"] == 20


@pytest.mark.asyncio
async def test_final_standings_are_not_recomputed(league):
    repo = AsyncMock()
    repo.get_standings.return_value = [{"team_id": "a", "final": True}]
    await recompute_division(repo, "s1", "d1")
    repo.replace_standings.assert_not_awaited()
