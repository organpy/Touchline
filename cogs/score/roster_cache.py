"""The teams and rosters score reporting works with, as a read-only view of ``TeamPlayerCache``.

It keeps no data and runs no queries of its own: ``TeamPlayerCache`` is reloaded whenever a team or roster
changes, so this view is always current. Imported independently to avoid circular imports.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.team_player_cache import TeamPlayerCache


@dataclass
class CachedPlayer:
    """Cached player info"""
    player_id: str
    display_name: str
    discord_id: Optional[int] = None


@dataclass
class CachedTeam:
    """Cached team info"""
    team_id: str
    code: str
    name: str
    emoji: Optional[str] = None
    division_id: Optional[str] = None
    active_players: List[CachedPlayer] = None

    def __post_init__(self):
        if self.active_players is None:
            self.active_players = []


def _player(raw: Dict[str, Any]) -> CachedPlayer:
    return CachedPlayer(player_id=raw["id"], display_name=raw["display_name"], discord_id=raw.get("discord_id"))


def _team(raw: Dict[str, Any]) -> CachedTeam:
    return CachedTeam(
        team_id=raw["id"],
        code=raw["code"],
        name=raw["name"],
        emoji=raw.get("emoji"),
        division_id=raw.get("division_id"),
        active_players=[_player(p) for p in TeamPlayerCache.get_team_players(raw["id"])],
    )


class RosterCache:
    """Teams (not archived) with the players on their roster in the active season."""

    @property
    def loaded(self) -> bool:
        return TeamPlayerCache.is_loaded()

    async def load_cache(self, supabase_client) -> None:
        """Load the team cache when it is not loaded yet (it normally is, from startup)."""
        if not TeamPlayerCache.is_loaded():
            await TeamPlayerCache.refresh(supabase_client)

    def get_team_by_code(self, code: str) -> Optional[CachedTeam]:
        """Get team by code (codes are lowercase slugs)"""
        if not code:
            return None
        raw = TeamPlayerCache.get_team_by_code(code.strip())
        return _team(raw) if raw and not raw.get("archived_at") else None

    def teams_in_same_league(self, team_code1: str, team_code2: str) -> bool:
        """Check if two teams are in the same league (division)"""
        team1 = self.get_team_by_code(team_code1)
        team2 = self.get_team_by_code(team_code2)

        if not team1 or not team2:
            return False

        if not team1.division_id or not team2.division_id:
            return False

        return team1.division_id == team2.division_id

    def get_all_teams(self) -> List[CachedTeam]:
        """Get all cached teams"""
        return [_team(raw) for raw in TeamPlayerCache.get_all_teams()]

    def get_team_players(self, team_code: str) -> List[CachedPlayer]:
        """Get active players for a team"""
        team = self.get_team_by_code(team_code)
        return team.active_players if team else []

    def get_player_by_id(self, player_id: str) -> Optional[CachedPlayer]:
        """Get a player who is on a team's roster this season, by ID"""
        raw = TeamPlayerCache.get_player_by_id(player_id)
        if not raw or not TeamPlayerCache.get_team_by_player_id(player_id):
            return None
        return _player(raw)

    def search_players(self, team_code: str, query: str) -> List[CachedPlayer]:
        """Search players on a team by name (for autocomplete)"""
        query_lower = query.lower()
        return [p for p in self.get_team_players(team_code) if query_lower in p.display_name.lower()]
