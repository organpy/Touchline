import pytest

from utils.team_colors import (
    get_team_color,
    get_division_color,
    get_default_color,
    resolve_color,
    clear_cache,
)


def test_default_color_valid():
    clear_cache()
    default_int = get_default_color()
    assert isinstance(default_int, int)
    # #5865F2 -> 0x5865F2
    assert default_int == int("5865F2", 16)


def test_division_colors_present():
    clear_cache()
    assert get_division_color("NBL") == int("C08E56", 16)
    assert get_division_color("MBL") == int("FF5D20", 16)
    assert get_division_color("NCBA") == int("00ADB5", 16)


def test_team_color_initially_none():
    clear_cache()
    # Teams are skeleton placeholders; should resolve None so fallback chain applies
    assert get_team_color("titans") is None


def test_resolve_priority_force():
    clear_cache()
    color = resolve_color(team_code="titans", division_code="NBL", force_color="#FFFFFF")
    assert color == int("FFFFFF", 16)


def test_resolve_priority_team_over_division():
    clear_cache()
    # Inject fake team color via monkeypatch of internal cache
    from utils import team_colors as tc
    tc._loaded = True
    tc._team_colors["titans"] = "#123456"
    tc._division_colors["NBL"] = "#C08E56"
    c = resolve_color(team_code="titans", division_code="NBL")
    assert c == int("123456", 16)


def test_resolve_priority_division_over_default():
    clear_cache()
    c = resolve_color(team_code=None, division_code="MBL")
    assert c == int("FF5D20", 16)


def test_resolve_fallback_default():
    clear_cache()
    c = resolve_color(team_code=None, division_code=None)
    assert c == get_default_color()
