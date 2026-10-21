import asyncio
from types import SimpleNamespace

from utils.team_player_cache import TeamPlayerCache


class FakeQuery:
    def __init__(self, data):
        self._data = [dict(row) for row in data]
        self._filters = []

    def select(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def is_(self, column, value):
        self._filters.append(("is", column, value))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, set(values)))
        return self

    def execute(self):
        rows = [dict(row) for row in self._data]
        for kind, column, value in self._filters:
            if kind == "eq":
                rows = [row for row in rows if row.get(column) == value]
            elif kind == "is":
                if value is None or value == "null":
                    rows = [row for row in rows if row.get(column) is None]
                else:
                    rows = [row for row in rows if row.get(column) == value]
            elif kind == "in":
                rows = [row for row in rows if row.get(column) in value]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        data = self._tables.get(name, [])
        return FakeQuery(data)


def test_cache_refresh_enriches_team_and_player_metadata():
    fake_supabase = FakeSupabase(
        {
            "divisions": [
                {"id": "div-1", "code": "D1", "name": "Premier", "tier": 1},
            ],
            "division_groups": [],
            "seasons": [
                {"id": "season-1", "number": 1, "name": "Season 1", "phase": "regular", "active": True},
            ],
            "season_teams": [
                {"season_id": "season-1", "team_id": "team-1", "division_id": "div-1", "group_id": None},
            ],
            "teams": [
                {"id": "team-1", "code": "pandas", "name": "Pandas", "color": "#112233"},
            ],
            "players": [
                {"id": "player-1", "display_name": "Ace", "discord_id": 111},
            ],
            "rosters": [
                {
                    "id": "roster-1",
                    "team_id": "team-1",
                    "player_id": "player-1",
                    "season_id": "season-1",
                    "joined_at": "2025-01-01T00:00:00Z",
                    "left_at": None,
                    "players": {"id": "player-1", "display_name": "Ace", "discord_id": 111},
                    "teams": {"id": "team-1", "code": "pandas"},
                }
            ],
        }
    )

    TeamPlayerCache.invalidate()
    TeamPlayerCache.set_supabase(fake_supabase)

    asyncio.run(TeamPlayerCache.refresh())

    team = TeamPlayerCache.get_team_by_code("PANDAS")
    assert team is not None
    assert team.get("division_id") == "div-1"
    assert team.get("league_type") == "D1"
    assert team.get("active_season_id") == "season-1"
    assert team.get("color_primary") == 0x112233

    player = TeamPlayerCache.get_player_by_name("Ace")
    assert player is not None
    assert player.get("current_team_code") == "pandas"
    membership = player.get("membership")
    assert membership and membership["season_id"] == "season-1"
    assert membership["league_type"] == "D1"

    TeamPlayerCache.invalidate()
    from utils.league_cache import LeagueCache
    LeagueCache.invalidate()


def test_is_fresh_after_refresh():
    from datetime import datetime, timedelta, timezone

    try:
        TeamPlayerCache._last_refresh = datetime.now(timezone.utc)
        assert TeamPlayerCache.is_fresh() is True

        TeamPlayerCache._last_refresh = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert TeamPlayerCache.is_fresh() is False
    finally:
        TeamPlayerCache.invalidate()
