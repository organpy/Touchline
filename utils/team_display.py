"""Utility helpers for consistent team display strings."""

from __future__ import annotations

from typing import Dict, Optional

from utils.team_player_cache import TeamPlayerCache

DEFAULT_TEAM_EMOJI = "🏀"


def _resolve_team(team: Optional[Dict[str, object]]) -> Dict[str, object]:
    return team or {}


def format_team_label_from_record(team: Optional[Dict[str, object]], fallback: str = "Unknown Team") -> str:
    """Return '<emoji> Team Name' for a cached team record."""
    record = _resolve_team(team)
    emoji = record.get("emoji") or DEFAULT_TEAM_EMOJI
    name = record.get("name") or fallback
    return f"{emoji} {name}"


def format_team_label_by_id(team_id: Optional[str], fallback: str = "Unknown Team") -> str:
    """Look up a team by id and return formatted label."""
    if not team_id:
        return format_team_label_from_record(None, fallback)
    cache = TeamPlayerCache()
    team = cache.get_team(team_id)
    return format_team_label_from_record(team, fallback)


def format_team_label_by_code(team_code: Optional[str], fallback: str = "Unknown Team") -> str:
    """Look up a team by code and return formatted label."""
    if not team_code:
        return format_team_label_from_record(None, fallback)
    cache = TeamPlayerCache()
    team = cache.get_team_by_code(team_code)
    return format_team_label_from_record(team, fallback)
