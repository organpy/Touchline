from utils.league_cache import LeagueCache
from utils.team_colors import get_default_color, get_division_color, get_team_color, resolve_color
from utils.team_player_cache import TeamPlayerCache


def setup_function():
    LeagueCache.load_snapshot(
        seasons=[],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "color": "#F7AD44"},
            {"id": "d2", "code": "D2", "name": "Championship", "tier": 2, "color": None},
        ],
    )
    TeamPlayerCache.invalidate()
    TeamPlayerCache._store_team({"id": "t1", "code": "titans", "name": "Titans", "color": "#123456"})
    TeamPlayerCache._store_team({"id": "t2", "code": "plain", "name": "Plain", "color": None})


def teardown_function():
    LeagueCache.invalidate()
    TeamPlayerCache.invalidate()


def test_default_color_valid():
    assert get_default_color() == 0x5865F2


def test_division_color_from_league_cache():
    assert get_division_color("D1") == 0xF7AD44
    assert get_division_color("d1") == 0xF7AD44
    assert get_division_color("D2") is None
    assert get_division_color("nope") is None


def test_team_color_from_team_cache():
    assert get_team_color("titans") == 0x123456
    assert get_team_color("plain") is None
    assert get_team_color("nonexistent-team") is None


def test_resolve_priority_force():
    assert resolve_color(team_code="titans", division_code="D1", force_color="#FFFFFF") == 0xFFFFFF


def test_resolve_priority_team_over_division():
    assert resolve_color(team_code="titans", division_code="D1") == 0x123456


def test_resolve_priority_division_over_default():
    assert resolve_color(team_code="plain", division_code="D1") == 0xF7AD44


def test_resolve_fallback_default():
    assert resolve_color(team_code=None, division_code=None) == get_default_color()
