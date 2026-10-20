"""Tests for postseason bracket generation and playoff outcomes."""

import pytest

from services.brackets import (
    KIND_CHAMPIONSHIP,
    KIND_PROMO_REL,
    KIND_PROMOTION,
    KIND_RELEGATION,
    build_postseason_plan,
    championship_bracket,
    cross_bracket,
    snake,
    split_bracket,
    standard_order,
)
from services.division_settings import ChampionshipConfig
from services.movements import outcome_from_brackets
from utils.league_cache import LeagueCache


def rows(prefix, count, zone=None, zones=None, groups=None):
    result = []
    for i in range(count):
        result.append({
            "team_id": f"{prefix}{i + 1}",
            "rank": i + 1,
            "zone": (zones or {}).get(i + 1, zone),
            "group_id": (groups or {}).get(i + 1),
            "group_rank": None,
        })
    return result


def by_key(bracket):
    return {s["key"]: s for s in bracket["series"]}


def first_round(bracket):
    return sorted(
        ((s["team1_seed"], s["team2_seed"]) for s in bracket["series"] if s["round"] == 1),
    )


def test_standard_order():
    assert standard_order(2) == [1, 2]
    assert standard_order(4) == [1, 4, 2, 3]
    assert standard_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_snake_distribution():
    assert snake([1, 2, 3, 4, 5, 6], 2) == [[1, 4, 5], [2, 3, 6]]


def test_eight_team_championship_is_a_full_bracket():
    bracket = championship_bracket("d1", rows("t", 8, zone="champion_playoff"), ChampionshipConfig(enabled=True, teams=8, best_of_by_round=[1, 3, 5]))
    assert bracket["kind"] == KIND_CHAMPIONSHIP
    assert first_round(bracket) == [(1, 8), (2, 7), (3, 6), (4, 5)]
    rounds = sorted({s["round"] for s in bracket["series"]})
    assert rounds == [1, 2, 3]
    assert {s["best_of"] for s in bracket["series"] if s["round"] == 3} == {5}
    final = [s for s in bracket["series"] if s["round"] == 3][0]
    feeders = [s for s in bracket["series"] if s["winner_to"] == final["key"]]
    assert sorted(f["winner_slot"] for f in feeders) == [1, 2]
    assert [(s["round"], s["position"]) for s in bracket["series"]] == [(1, 0), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1), (3, 0)]


def test_six_team_championship_gives_top_two_seeds_byes():
    bracket = championship_bracket("d1", rows("t", 6, zone="champion_playoff"), ChampionshipConfig(enabled=True, teams=6))
    assert first_round(bracket) == [(3, 6), (4, 5)]
    semis = [s for s in bracket["series"] if s["round"] == 2]
    assert sorted(s["team1_seed"] for s in semis) == [1, 2]   # seeds 1 and 2 wait in the semis
    assert all(s["team2_id"] is None for s in semis)
    assert len(bracket["series"]) == 5


def test_by_group_seeding_puts_group_winners_first():
    standings = rows("t", 4, zone="champion_playoff", groups={1: "A", 2: "A", 3: "B", 4: "B"})
    for row, group_rank in zip(standings, (1, 2, 1, 2)):
        row["group_rank"] = group_rank
    bracket = championship_bracket("d1", standings, ChampionshipConfig(enabled=True, teams=4, seeding="by_group"))
    seeds = {e["team_id"]: e["seed"] for e in bracket["entries"]}
    assert seeds == {"t1": 1, "t3": 2, "t2": 3, "t4": 4}


def test_promotion_bracket_has_one_sub_bracket_per_spot():
    bracket = split_bracket(KIND_PROMOTION, "d2", rows("p", 6, zone="promotion_playoff"), spots=2, best_of=3)
    assert bracket["spots"] == 2
    roots = [s for s in bracket["series"] if not s["winner_to"]]
    assert len(roots) == 2
    # 6 teams for 2 spots: sub-brackets of 4 slots, seeds 1 and 2 get byes
    assert len(bracket["series"]) == 4
    assert all(s["best_of"] == 3 for s in bracket["series"])
    bye_teams = {s["team1_seed"] for s in roots}
    assert bye_teams == {1, 2}


def test_relegation_bracket_sends_losers_forward():
    bracket = split_bracket(KIND_RELEGATION, "d1", rows("r", 4, zone="relegation_playoff"), spots=1, best_of=1)
    first = [s for s in bracket["series"] if s["round"] == 1]
    final = [s for s in bracket["series"] if s["round"] == 2][0]
    assert all(s["loser_to"] == final["key"] and s["winner_to"] is None for s in first)
    assert first_round(bracket) == [(1, 4), (2, 3)]


def test_cross_bracket_pairs_best_upper_with_worst_lower():
    upper = rows("u", 6, zones={5: "relegation_playoff", 6: "relegation_playoff"})
    lower = rows("l", 6, zones={1: "promotion_playoff", 2: "promotion_playoff"})
    bracket = cross_bracket("d1", "d2", upper, lower, spots=2, best_of=3)
    assert bracket["kind"] == KIND_PROMO_REL
    assert bracket["lower_division_id"] == "d2"
    pairs = sorted((s["team1_id"], s["team2_id"]) for s in bracket["series"])
    assert pairs == [("u5", "l2"), ("u6", "l1")]


@pytest.fixture
def league():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "S1", "active": True, "phase": "regular"}],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "settings": {
                "championship": {"enabled": True, "teams": 4},
                "boundary_below": {"direct_swaps": 1, "playoff_swaps": 1, "mode": "separate",
                                   "upper_playoff_teams": 2, "lower_playoff_teams": 2},
            }},
            {"id": "d2", "code": "D2", "name": "Second", "tier": 2, "settings": {
                "boundary_below": {"playoff_swaps": 1, "mode": "cross"},
            }},
            {"id": "d3", "code": "D3", "name": "Third", "tier": 3, "settings": {}},
        ],
    )
    yield
    LeagueCache.invalidate()


def test_league_plan_has_every_configured_bracket(league):
    standings = {
        "d1": rows("a", 8, zones={1: "champion_playoff", 2: "champion_playoff", 3: "champion_playoff",
                                  4: "champion_playoff", 6: "relegation_playoff", 7: "relegation_playoff", 8: "relegated"}),
        "d2": rows("b", 6, zones={1: "promoted", 2: "promotion_playoff", 3: "promotion_playoff", 6: "relegation_playoff"}),
        "d3": rows("c", 4, zones={1: "promotion_playoff"}),
    }
    plan = build_postseason_plan("s1", standings)
    kinds = sorted((b["kind"], b["division_id"]) for b in plan["brackets"])
    assert kinds == [
        (KIND_CHAMPIONSHIP, "d1"),
        (KIND_PROMO_REL, "d2"),
        (KIND_PROMOTION, "d2"),
        (KIND_RELEGATION, "d1"),
    ]
    keys = [s["key"] for b in plan["brackets"] for s in b["series"]]
    assert len(keys) == len(set(keys))


def test_outcome_reads_champion_and_playoff_moves(league):
    brackets = [
        {"kind": KIND_CHAMPIONSHIP, "division_id": "d1", "series": [
            {"round": 1, "position": 0, "winner_to": "x", "winner_id": "a1"},
            {"round": 2, "position": 0, "winner_to": None, "winner_id": "a2"},
        ]},
        {"kind": KIND_RELEGATION, "division_id": "d1", "series": [
            {"round": 1, "position": 0, "loser_to": None, "winner_id": "a6", "loser_id": "a7"},
        ]},
        {"kind": KIND_PROMOTION, "division_id": "d2", "series": [
            {"round": 1, "position": 0, "winner_to": None, "winner_id": "b3", "loser_id": "b2"},
        ]},
        {"kind": KIND_PROMO_REL, "division_id": "d2", "lower_division_id": "d3",
         "bracket_entries": [{"team_id": "b6", "source_division_id": "d2"}, {"team_id": "c1", "source_division_id": "d3"}],
         "series": [{"round": 1, "position": 0, "winner_id": "c1", "loser_id": "b6"}]},
    ]
    champion, moves = outcome_from_brackets(brackets)
    assert champion == "a2"
    assert [(m.team_id, m.from_division_id, m.to_division_id, m.reason) for m in moves] == [
        ("a7", "d1", "d2", "relegated_playoff"),
        ("b3", "d2", "d1", "promoted_playoff"),
        ("c1", "d3", "d2", "promoted_playoff"),
        ("b6", "d2", "d3", "relegated_playoff"),
    ]


def test_upper_team_holding_on_in_cross_playoff_moves_nobody(league):
    brackets = [{"kind": KIND_PROMO_REL, "division_id": "d2", "lower_division_id": "d3",
                 "bracket_entries": [{"team_id": "b6", "source_division_id": "d2"}, {"team_id": "c1", "source_division_id": "d3"}],
                 "series": [{"round": 1, "position": 0, "winner_id": "b6", "loser_id": "c1"}]}]
    assert outcome_from_brackets(brackets) == (None, [])
