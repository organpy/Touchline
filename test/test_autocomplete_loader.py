import pytest

from utils import autocomplete_loader as loader


@pytest.fixture(autouse=True)
def reset_autocomplete_cache():
    """Ensure autocomplete cache is cleared between tests."""
    loader.clear_autocomplete_cache()
    yield
    loader.clear_autocomplete_cache()


def test_get_team_codes_contains_known_values():
    codes = loader.get_team_codes()
    assert 'titans' in codes
    assert 'the-warriors' in codes
    assert 'south-point-sharks' in codes


def test_load_autocomplete_data_is_cached(monkeypatch):
    call_count = 0

    def fake_loader():
        nonlocal call_count
        call_count += 1
        return {"limits": [{"name": "1", "value": "1"}]}

    monkeypatch.setattr(loader, "_load_data_from_disk", fake_loader)

    loader.load_autocomplete_data()
    loader.load_autocomplete_data()

    assert call_count == 1, "should reuse cached value within TTL"


def test_load_autocomplete_data_refreshes_after_ttl(monkeypatch):
    first_payload = {"limits": [{"name": "3", "value": "3"}]}
    second_payload = {"limits": [{"name": "5", "value": "5"}]}

    loader_sequence = [first_payload, second_payload]

    def fake_loader():
        return loader_sequence.pop(0)

    monkeypatch.setattr(loader, "_load_data_from_disk", fake_loader)

    initial = loader.load_autocomplete_data()
    assert initial["limits"][0]["name"] == "3"

    # Force cache to appear expired
    loader._AUTOCOMPLETE_LAST_LOADED -= loader.AUTOCOMPLETE_CACHE_TTL_SECONDS + 1

    refreshed = loader.load_autocomplete_data()
    assert refreshed["limits"][0]["name"] == "5"


def test_refresh_autocomplete_cache_forces_reload(monkeypatch):
    payloads = [
        {"metrics": [{"name": "points", "value": "points"}]},
        {"metrics": [{"name": "assists", "value": "assists"}]},
    ]

    def fake_loader():
        return payloads.pop(0)

    monkeypatch.setattr(loader, "_load_data_from_disk", fake_loader)

    cached = loader.load_autocomplete_data()
    assert cached["metrics"][0]["name"] == "points"

    refreshed = loader.refresh_autocomplete_cache()
    assert refreshed["metrics"][0]["name"] == "assists"

    # Verify global cache updated
    data = loader.load_autocomplete_data()
    assert data["metrics"][0]["name"] == "assists"
