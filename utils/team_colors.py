"""Team and division colours for embeds.

Colours come from the database (``teams.color`` and ``divisions.color``, ``#RRGGBB``) through
``TeamPlayerCache`` and ``LeagueCache``; set them in the dashboard.

- get_team_color(team_code)
- get_division_color(division_code)
- get_default_color()
- resolve_color(team_code=None, division_code=None, force_color=None)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_COLOR = "#5865F2"
HEX_ALLOWED = set("0123456789abcdefABCDEF")


def _validate_hex(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return False
    if not value.startswith("#") or len(value) not in (7, 4):
        return False
    return all(ch in HEX_ALLOWED for ch in value[1:])


def _hex_to_int(value: str) -> int:
    """Convert #RRGGBB or #RGB to int for discord.Color."""
    if len(value) == 4:  # #RGB -> #RRGGBB
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return int(value[1:], 16)


def _color_int(value: Optional[str]) -> Optional[int]:
    return _hex_to_int(value) if _validate_hex(value) else None


def get_team_color(team_code: Optional[str]) -> Optional[int]:
    if not team_code:
        return None
    from utils.team_player_cache import TeamPlayerCache

    team = TeamPlayerCache.get_team_by_code(team_code)
    return _color_int((team or {}).get("color"))


def get_division_color(division_code: Optional[str]) -> Optional[int]:
    if not division_code:
        return None
    from utils.league_cache import LeagueCache

    division = LeagueCache.find_division(division_code)
    return _color_int((division or {}).get("color"))


def get_default_color() -> int:
    return _hex_to_int(DEFAULT_COLOR)


def resolve_color(team_code: Optional[str] = None, division_code: Optional[str] = None, force_color: Optional[str] = None) -> int:
    """Resolve final color int applying priority: force_color -> team -> division -> default."""
    if force_color:
        if _validate_hex(force_color):
            return _hex_to_int(force_color)
        if isinstance(force_color, str) and force_color.isdigit():
            return int(force_color)
    team_val = get_team_color(team_code)
    if team_val is not None:
        return team_val
    division_val = get_division_color(division_code)
    if division_val is not None:
        return division_val
    return get_default_color()


def clear_cache() -> None:
    """Kept for callers that reset colours; colours now live in the league caches."""


__all__ = [
    "get_team_color",
    "get_division_color",
    "get_default_color",
    "resolve_color",
    "clear_cache",
]
