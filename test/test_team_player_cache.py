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
                {"id": "div-nbl", "league_type": "NBL", "name": "NBL Prime"},
            ],
            "seasons": [
                {"id": "season-1", "name": "S10", "division_id": "div-nbl", "active": True},
            ],
            "teams": [
                {"id": "team-1", "division_id": "div-nbl", "code": "PANDAS", "name": "Pandas"},
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
                    "teams": {"id": "team-1", "code": "PANDAS"},
                }
            ],
        }
    )

    TeamPlayerCache.invalidate()
    TeamPlayerCache.set_supabase(fake_supabase)

    asyncio.run(TeamPlayerCache.refresh())

    team = TeamPlayerCache.get_team_by_code("PANDAS")
    assert team is not None
    assert team.get("league_type") == "NBL"
    assert team.get("active_season_id") == "season-1"

    player = TeamPlayerCache.get_player_by_name("Ace")
    assert player is not None
    assert player.get("current_team_code") == "PANDAS"
    memberships = player.get("active_memberships")
    assert memberships and memberships[0]["season_id"] == "season-1"
    assert memberships[0]["league_type"] == "NBL"

    TeamPlayerCache.invalidate()
