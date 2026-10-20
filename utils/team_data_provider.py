"""
TeamDataProvider: team display metadata (code, name, emoji, division) for embeds and autocomplete.

Backed by ``TeamPlayerCache`` (teams from the database) and ``LeagueCache`` (each team's division
this season). "Region" is the division code.
"""

import logging
from typing import Dict, List, Optional

from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


def _entry(team: Dict) -> Dict[str, str]:
    division = LeagueCache.team_division(team.get("id")) or {}
    return {
        "code": (team.get("code") or "").lower(),
        "display_name": team.get("name") or team.get("code") or "",
        "emoji": team.get("emoji") or "",
        "region": division.get("code") or "",
    }


class TeamDataProvider:
    """Read-only view of the cached teams in a display-friendly shape."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_team(cls, team_code: Optional[str]) -> Optional[Dict[str, str]]:
        """{code, display_name, emoji, region} for a team code, or None."""
        if not team_code:
            return None
        team = TeamPlayerCache.get_team_by_code(team_code.strip().lower())
        return _entry(team) if team else None

    @classmethod
    def get_team_emoji(cls, team_code: Optional[str]) -> Optional[str]:
        team = cls.get_team(team_code)
        return (team.get("emoji") or None) if team else None

    @classmethod
    def get_team_name(cls, team_code: Optional[str]) -> Optional[str]:
        team = cls.get_team(team_code)
        return team.get("display_name") if team else None

    @classmethod
    def get_team_region(cls, team_code: Optional[str]) -> Optional[str]:
        team = cls.get_team(team_code)
        return (team.get("region") or None) if team else None

    @classmethod
    def get_code_by_name(cls, display_name: Optional[str]) -> Optional[str]:
        if not display_name:
            return None
        team = TeamPlayerCache.find_team(display_name)
        return team.get("code") if team else None

    @classmethod
    def get_all_codes(cls) -> List[str]:
        return sorted(team["code"] for team in cls.get_all_teams())

    @classmethod
    def get_codes_by_region(cls, region: Optional[str]) -> List[str]:
        if not region:
            return []
        return [team["code"] for team in cls.get_all_teams() if team["region"].lower() == region.lower()]

    @classmethod
    def get_all_teams(cls) -> List[Dict[str, str]]:
        """Active teams sorted by division tier, then name."""
        teams = TeamPlayerCache.get_all_teams()

        def sort_key(team: Dict) -> tuple:
            division = LeagueCache.team_division(team.get("id")) or {}
            return (division.get("tier") or 999, (team.get("name") or "").lower())

        return [_entry(team) for team in sorted(teams, key=sort_key)]

    @classmethod
    def get_regions(cls) -> List[str]:
        """Division codes in tier order."""
        return [d.get("code") for d in LeagueCache.divisions() if d.get("code")]

    @classmethod
    def format_team_display(cls, team_code: Optional[str]) -> str:
        """'Name (CODE)'."""
        team = cls.get_team(team_code)
        if not team:
            return ""
        return f"{team['display_name']} ({team['code'].upper()})"

    @classmethod
    def format_team_with_emoji(cls, team_code: Optional[str]) -> str:
        """Display name (kept for callers that format choices)."""
        team = cls.get_team(team_code)
        return team["display_name"] if team else ""
