"""
TeamDataProvider: Single source of truth for team metadata from teams.json.
Provides cached access to team emojis, display names, codes, and full info.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
TEAMS_FILE = ROOT_DIR / "teams.json"


class TeamDataProvider:
    """Singleton provider for team data with caching."""
    
    _instance = None
    _teams_cache: Dict[str, Dict[str, str]] = {}  # code -> {code, name, emoji, region}
    _league_map: Dict[str, List[str]] = {}  # region -> [team_codes]
    _name_to_code: Dict[str, str] = {}  # display_name (lower) -> code
    _loaded = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _load(cls) -> None:
        """Load and cache teams.json data."""
        if cls._loaded:
            return

        try:
            with open(TEAMS_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except FileNotFoundError:
            logger.warning(f"teams.json not found at {TEAMS_FILE}")
            cls._loaded = True
            return
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse teams.json: {e}")
            cls._loaded = True
            return

        # Normalize structure: region -> {name -> {code, emoji}}
        for region, teams in (raw_data or {}).items():
            cls._league_map[region] = []
            for display_name, team_data in (teams or {}).items():
                code = team_data.get("code", "").lower().strip()
                emoji = team_data.get("emoji", "")

                if not code:
                    logger.warning(f"Team {display_name} in {region} has no code")
                    continue

                # Store normalized team info
                cls._teams_cache[code] = {
                    "code": code,
                    "display_name": display_name,
                    "emoji": emoji,
                    "region": region,
                }

                # Add to league map
                cls._league_map[region].append(code)

                # Build name-to-code mapping (case-insensitive)
                cls._name_to_code[display_name.lower()] = code

        logger.info(f"✅ Loaded {len(cls._teams_cache)} teams from teams.json")
        cls._loaded = True

    @classmethod
    def get_team(cls, team_code: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Get full team info by code.
        Returns: {code, display_name, emoji, region} or None
        """
        if not team_code:
            return None

        cls._load()
        return cls._teams_cache.get(team_code.lower().strip())

    @classmethod
    def get_team_emoji(cls, team_code: Optional[str]) -> Optional[str]:
        """Get team emoji by code."""
        team = cls.get_team(team_code)
        return team.get("emoji") if team else None

    @classmethod
    def get_team_name(cls, team_code: Optional[str]) -> Optional[str]:
        """Get team display name by code."""
        team = cls.get_team(team_code)
        return team.get("display_name") if team else None

    @classmethod
    def get_team_region(cls, team_code: Optional[str]) -> Optional[str]:
        """Get team region/league by code."""
        team = cls.get_team(team_code)
        return team.get("region") if team else None

    @classmethod
    def get_code_by_name(cls, display_name: Optional[str]) -> Optional[str]:
        """Get team code by display name (case-insensitive)."""
        if not display_name:
            return None

        cls._load()
        return cls._name_to_code.get(display_name.lower().strip())

    @classmethod
    def get_all_codes(cls) -> List[str]:
        """Get all team codes."""
        cls._load()
        return sorted(cls._teams_cache.keys())

    @classmethod
    def get_codes_by_region(cls, region: Optional[str]) -> List[str]:
        """Get all team codes for a region/league."""
        if not region:
            return []

        cls._load()
        return cls._league_map.get(region, [])

    @classmethod
    def get_all_teams(cls) -> List[Dict[str, str]]:
        """Get all teams as list of dicts."""
        cls._load()
        return sorted(
            cls._teams_cache.values(),
            key=lambda t: (t.get("region", ""), t.get("code", ""))
        )

    @classmethod
    def get_regions(cls) -> List[str]:
        """Get all regions/leagues."""
        cls._load()
        return sorted(cls._league_map.keys())

    @classmethod
    def format_team_display(cls, team_code: Optional[str]) -> str:
        """Format team display as 'Name (CODE)'."""
        team = cls.get_team(team_code)
        if not team:
            return ""
        return f"{team['display_name']} ({team['code'].upper()})"

    @classmethod
    def format_team_with_emoji(cls, team_code: Optional[str]) -> str:
        """Format team display with emoji if available."""
        team = cls.get_team(team_code)
        if not team:
            return ""
        # Return only display name without emoji or code
        return team['display_name']
