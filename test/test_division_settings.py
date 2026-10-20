from services.division_settings import (
    BoundaryConfig,
    ChampionshipConfig,
    DivisionSettings,
    describe_boundary,
    describe_championship,
    describe_rules,
    rounds_needed,
)


def test_defaults_from_empty_settings():
    settings = DivisionSettings.from_dict(None)
    assert settings.scoring_mode == "points_scored"
    assert settings.games_per_matchup == 2
    assert settings.picks_enabled is False
    assert settings.championship.enabled is False
    assert settings.boundary_below.is_active is False
    assert settings.validate() == []


def test_parsing_is_tolerant_of_bad_values():
    settings = DivisionSettings.from_dict({
        "scoring_mode": "nonsense",
        "points_per_win": "x",
        "games_per_matchup": None,
        "championship": {"teams": "8", "seeding": "random", "best_of_by_round": []},
        "boundary_below": {"direct_swaps": -3, "mode": "weird"},
        "unknown": True,
    })
    assert settings.scoring_mode == "points_scored"
    assert settings.points_per_win == 3
    assert settings.games_per_matchup == 2
    assert settings.championship.teams == 8
    assert settings.championship.seeding == "overall"
    assert settings.championship.best_of_by_round == [1]
    assert settings.boundary_below.direct_swaps == 0
    assert settings.boundary_below.mode == "separate"


def test_round_trip():
    original = DivisionSettings(
        scoring_mode="win_points",
        points_per_win=2,
        games_per_matchup=3,
        picks_enabled=True,
        championship=ChampionshipConfig(enabled=True, teams=6, seeding="by_group", best_of_by_round=[1, 3, 5]),
        boundary_below=BoundaryConfig(direct_swaps=1, playoff_swaps=1, upper_playoff_teams=4, lower_playoff_teams=4, best_of=3),
    )
    assert DivisionSettings.from_dict(original.to_dict()) == original


def test_competition_points_by_mode():
    assert DivisionSettings(scoring_mode="points_scored").competition_points(wins=3, points_for=40) == 40
    assert DivisionSettings(scoring_mode="win_points", points_per_win=3).competition_points(wins=3, points_for=40) == 9


def test_championship_best_of_repeats_last_round_value():
    config = ChampionshipConfig(enabled=True, teams=8, best_of_by_round=[1, 3])
    assert [config.best_of(r) for r in (1, 2, 3)] == [1, 3, 3]
    assert config.rounds == 3


def test_championship_validation():
    assert ChampionshipConfig(enabled=True, teams=1).validate()
    assert ChampionshipConfig(enabled=True, teams=17).validate()
    assert ChampionshipConfig(enabled=True, teams=6, best_of_by_round=[2]).validate()
    assert ChampionshipConfig(enabled=True, teams=6, best_of_by_round=[1, 3]).validate() == []
    # Disabled configs are never invalid.
    assert ChampionshipConfig(enabled=False, teams=99).validate() == []


def test_relegation_bracket_sizes_have_no_byes():
    def errors(upper, spots=1, lower=4):
        return BoundaryConfig(playoff_swaps=spots, upper_playoff_teams=upper, lower_playoff_teams=lower).validate()

    assert errors(2) == []
    assert errors(4) == []
    assert errors(8) == []
    assert errors(3)
    assert errors(6)
    assert errors(1)
    assert errors(4, spots=2) == []
    assert errors(6, spots=2)  # 6 / 2 = 3, not a power of two


def test_promotion_bracket_needs_two_teams_per_spot():
    assert BoundaryConfig(playoff_swaps=1, upper_playoff_teams=2, lower_playoff_teams=3).validate() == []
    assert BoundaryConfig(playoff_swaps=1, upper_playoff_teams=2, lower_playoff_teams=1).validate()
    assert BoundaryConfig(playoff_swaps=2, upper_playoff_teams=4, lower_playoff_teams=3).validate()


def test_cross_mode_uses_one_pair_per_spot():
    config = BoundaryConfig.from_dict({"playoff_swaps": 2, "mode": "cross", "upper_playoff_teams": 7})
    assert config.upper_playoff_teams == 2
    assert config.lower_playoff_teams == 2
    assert config.validate() == []


def test_no_playoff_clears_playoff_teams():
    config = BoundaryConfig.from_dict({"direct_swaps": 2, "upper_playoff_teams": 4, "lower_playoff_teams": 4})
    assert config.upper_playoff_teams == 0 and config.lower_playoff_teams == 0
    assert config.total_swaps == 2


def test_settings_validation_ranges():
    assert DivisionSettings(points_per_win=0).validate()
    assert DivisionSettings(games_per_matchup=11).validate()
    assert DivisionSettings(boundary_below=BoundaryConfig(best_of=4)).validate()


def test_rounds_needed():
    assert rounds_needed(2, 1) == 1
    assert rounds_needed(3, 1) == 2
    assert rounds_needed(8, 1) == 3
    assert rounds_needed(4, 2) == 1
    assert rounds_needed(5, 2) == 2


def test_descriptions():
    assert "pts per win" in describe_rules(DivisionSettings(scoring_mode="win_points"))
    assert describe_boundary(BoundaryConfig()) == "No promotion/relegation"
    assert "2 automatic" in describe_boundary(BoundaryConfig(direct_swaps=2))
    assert "cross-tier" in describe_boundary(BoundaryConfig.from_dict({"playoff_swaps": 1, "mode": "cross"}))
    assert describe_championship(ChampionshipConfig()) == "No championship playoff"
    assert "8 teams" in describe_championship(ChampionshipConfig(enabled=True, teams=8))
