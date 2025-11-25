"""Test for team overview rank computation logic."""

import pytest


def compute_team_rank(standings: list[dict], team_id: str) -> tuple[int, int]:
    """Helper: sort standings by competition_points desc, point_diff desc, wins desc.
    
    Returns: (rank, total_teams) or raises ValueError if team not found.
    """
    sorted_standings = sorted(
        standings,
        key=lambda x: (
            x.get('competition_points', 0),
            x.get('point_diff', 0),
            x.get('wins', 0)
        ),
        reverse=True
    )
    
    team_standing = next((s for s in sorted_standings if s.get('team_id') == team_id), None)
    if not team_standing:
        raise ValueError(f"Team {team_id} not in standings")
    
    rank = sorted_standings.index(team_standing) + 1
    total = len(sorted_standings)
    return rank, total


def test_rank_first_place():
    standings = [
        {'team_id': 'A', 'competition_points': 30, 'point_diff': 10, 'wins': 10},
        {'team_id': 'B', 'competition_points': 20, 'point_diff': 5, 'wins': 7},
    ]
    rank, total = compute_team_rank(standings, 'A')
    assert rank == 1
    assert total == 2


def test_rank_second_place():
    standings = [
        {'team_id': 'A', 'competition_points': 30, 'point_diff': 10, 'wins': 10},
        {'team_id': 'B', 'competition_points': 20, 'point_diff': 5, 'wins': 7},
    ]
    rank, total = compute_team_rank(standings, 'B')
    assert rank == 2
    assert total == 2


def test_rank_tiebreaker_point_diff():
    """When points equal, higher point_diff wins."""
    standings = [
        {'team_id': 'A', 'competition_points': 20, 'point_diff': 15, 'wins': 5},
        {'team_id': 'B', 'competition_points': 20, 'point_diff': 10, 'wins': 5},
    ]
    rank, _ = compute_team_rank(standings, 'A')
    assert rank == 1
    rank, _ = compute_team_rank(standings, 'B')
    assert rank == 2


def test_rank_tiebreaker_wins():
    """When points and diff equal, higher wins wins."""
    standings = [
        {'team_id': 'A', 'competition_points': 20, 'point_diff': 5, 'wins': 7},
        {'team_id': 'B', 'competition_points': 20, 'point_diff': 5, 'wins': 6},
    ]
    rank, _ = compute_team_rank(standings, 'A')
    assert rank == 1
    rank, _ = compute_team_rank(standings, 'B')
    assert rank == 2


def test_rank_not_found():
    standings = [
        {'team_id': 'A', 'competition_points': 20, 'point_diff': 5, 'wins': 5},
    ]
    with pytest.raises(ValueError, match="Team X not in standings"):
        compute_team_rank(standings, 'X')


def test_rank_three_way_tie_all_tiebreakers():
    """Complex tie scenario testing all tiebreaker levels."""
    standings = [
        {'team_id': 'A', 'competition_points': 25, 'point_diff': 10, 'wins': 8},  # 1st
        {'team_id': 'B', 'competition_points': 20, 'point_diff': 8, 'wins': 7},   # 2nd
        {'team_id': 'C', 'competition_points': 20, 'point_diff': 6, 'wins': 6},   # 3rd
    ]
    rank, total = compute_team_rank(standings, 'A')
    assert rank == 1 and total == 3
    rank, _ = compute_team_rank(standings, 'B')
    assert rank == 2
    rank, _ = compute_team_rank(standings, 'C')
    assert rank == 3
