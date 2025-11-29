import io

from utils import standings_image_generator_deprecated as sig


def _sample_standings():
    return [
        {
            "code": "PANDAS",
            "name": "Pandas",
            "wins": 10,
            "losses": 2,
            "points_for": 500,
            "points_against": 420,
            "point_diff": 80,
        },
        {
            "code": "EAGLES",
            "name": "Eagles",
            "wins": 8,
            "losses": 4,
            "points_for": 460,
            "points_against": 430,
            "point_diff": 30,
        },
    ]


class FakeCache:
    def __init__(self):
        self.store = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, key):
        self.get_calls += 1
        return self.store.get(key)

    def set(self, key, value):
        self.set_calls += 1
        self.store[key] = value

    def clear(self):
        self.store.clear()


def test_generate_standings_image_writes_to_cache(monkeypatch):
    sig.clear_standings_image_cache()
    fake_cache = FakeCache()
    monkeypatch.setattr(sig, "standings_image_cache", fake_cache, raising=False)
    monkeypatch.setattr(sig, "_find_logo_path", lambda *args: None)

    result = sig.generate_standings_image(_sample_standings(), "S10", "NBL Prime", "NBL")

    assert isinstance(result, io.BytesIO)
    assert fake_cache.set_calls == 1
    assert fake_cache.get_calls == 1  # retrieval happens before first render

    second_result = sig.generate_standings_image(_sample_standings(), "S10", "NBL Prime", "NBL")
    assert isinstance(second_result, io.BytesIO)
    assert fake_cache.get_calls == 2
    assert fake_cache.set_calls == 1, "cache hit should skip storing a second time"
    assert second_result.getvalue() == result.getvalue()


def test_clear_standings_image_cache_resets_storage():
    sig.clear_standings_image_cache()
    sig.standings_image_cache.set("test", b"123")
    assert sig.standings_image_cache.size() == 1

    sig.clear_standings_image_cache()
    assert sig.standings_image_cache.size() == 0
