"""
Autocomplete data loader and helper utilities.

Static choices (metrics, limits, stat labels) come from data/autocomplete_data.json; teams and
divisions come from the database caches.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUTOCOMPLETE_CACHE_TTL_SECONDS = 600  # 10 minutes

_AUTOCOMPLETE_DATA: Optional[Dict[str, Any]] = None
_AUTOCOMPLETE_LAST_LOADED: Optional[float] = None


def load_autocomplete_data(force_reload: bool = False) -> Dict[str, Any]:
    """Load autocomplete data from JSON file with TTL-based caching."""
    global _AUTOCOMPLETE_DATA, _AUTOCOMPLETE_LAST_LOADED

    if not force_reload and _AUTOCOMPLETE_DATA is not None:
        if _AUTOCOMPLETE_LAST_LOADED and not _is_cache_expired():
            return _AUTOCOMPLETE_DATA

    data = _load_data_from_disk()
    _AUTOCOMPLETE_DATA = data
    _AUTOCOMPLETE_LAST_LOADED = time.time()
    return data


def _is_cache_expired() -> bool:
    if _AUTOCOMPLETE_LAST_LOADED is None:
        return True
    return (time.time() - _AUTOCOMPLETE_LAST_LOADED) >= AUTOCOMPLETE_CACHE_TTL_SECONDS


def _load_data_from_disk() -> Dict[str, Any]:
    data_path = Path(__file__).parent.parent / "data" / "autocomplete_data.json"
    try:
        with open(data_path, "r", encoding="utf-8") as file:
            logger.debug("Loading autocomplete data from %s", data_path)
            return json.load(file)
    except FileNotFoundError:
        logger.error("autocomplete_data.json not found. Using defaults.")
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse autocomplete_data.json: %s", exc)

    # If we reach here we failed to load a valid file
    return get_default_autocomplete_data()


def refresh_autocomplete_cache() -> Dict[str, Any]:
    """Force refresh autocomplete data regardless of TTL."""
    return load_autocomplete_data(force_reload=True)


def clear_autocomplete_cache() -> None:
    """Clear cached autocomplete data (mainly for tests)."""
    global _AUTOCOMPLETE_DATA, _AUTOCOMPLETE_LAST_LOADED
    _AUTOCOMPLETE_DATA = None
    _AUTOCOMPLETE_LAST_LOADED = None


def get_default_autocomplete_data() -> Dict[str, Any]:
    """Return default autocomplete data if JSON file is unavailable."""
    return {
        "metrics": [
            {"name": "points", "value": "points"},
            {"name": "assists", "value": "assists"},
            {"name": "goals", "value": "goals"},
            {"name": "saves", "value": "saves"},
            {"name": "blocks", "value": "blocks"},
            {"name": "own_goals", "value": "own_goals"},
            {"name": "powerup_plays", "value": "pp_plays"},
        ],
        "limits": [
            {"name": "3", "value": "3"},
            {"name": "5", "value": "5"},
            {"name": "10", "value": "10"},
            {"name": "15", "value": "15"},
            {"name": "20", "value": "20"},
            {"name": "25", "value": "25"},
        ],
        "stat_fields": {
            "points": "Points",
            "assists": "Assists",
            "goals": "Goals",
            "saves": "Saves",
            "blocks": "Blocks",
            "own_goals": "Own Goals",
            "pp_plays": "Powerup Plays",
        }
    }


def get_metrics() -> List[Dict[str, str]]:
    """Get list of available metrics."""
    data = load_autocomplete_data()
    return data.get("metrics", [])


def get_metric_values() -> List[str]:
    """Get list of valid metric values."""
    return [m["value"] for m in get_metrics()]


def get_limits() -> List[Dict[str, str]]:
    """Get list of available limit options."""
    data = load_autocomplete_data()
    return data.get("limits", [])


def get_stat_field_display(metric: str) -> str:
    """Get display name for a metric."""
    data = load_autocomplete_data()
    stat_fields = data.get("stat_fields", {})
    return stat_fields.get(metric, metric.title().replace("_", " "))


def get_team_codes() -> List[str]:
    """All active team codes (from the team cache)."""
    from utils.team_player_cache import TeamPlayerCache

    return sorted(TeamPlayerCache.get_team_codes())


def get_league_entries() -> List[Dict[str, str]]:
    """Divisions in tier order as ``{"code", "name"}`` (from the league cache)."""
    from utils.league_cache import LeagueCache

    return [
        {"code": (d.get("code") or "").upper(), "name": d.get("name") or d.get("code") or ""}
        for d in LeagueCache.divisions()
    ]
