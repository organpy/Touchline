from utils.autocomplete_loader import get_team_codes


def test_get_team_codes_contains_known_values():
    codes = get_team_codes()
    assert 'titans' in codes
    assert 'the-warriors' in codes
    assert 'south-point-sharks' in codes
