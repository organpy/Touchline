"""Tests for the report model that is saved with record_match."""

from cogs.score.models import MatchEntry, PlayerStatEntry, TeamMatchEntry


def _entry():
    team1 = TeamMatchEntry(team_id="t1", team_code="a", team_name="A", score=9)
    team2 = TeamMatchEntry(team_id="t2", team_code="b", team_name="B", score=4)
    team1.add_player_stats(PlayerStatEntry(player_id="p1", player_name="Ace", goals=3, points=9))
    return MatchEntry(team1=team1, team2=team2)


def test_every_report_gets_its_own_id():
    assert _entry().report_id != _entry().report_id


def test_player_stats_for_the_database_function_carry_no_match_id():
    stats = _entry().to_supabase_player_stats()

    assert stats == [{
        "player_id": "p1", "goals": 3, "own_goals": 0, "points": 9, "assists": 0, "saves": 0, "blocks": 0,
        "powerup_plays": 0, "powerups_used": 0, "team_id": "t1",
    }]


def test_player_stats_can_still_carry_a_match_id():
    assert _entry().to_supabase_player_stats("m1")[0]["match_id"] == "m1"
