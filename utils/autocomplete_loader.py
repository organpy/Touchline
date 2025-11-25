"""
Autocomplete data loader and helper utilities.
Static data for autocomplete suggestions that rarely change.
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
        "special_options": {
            "all_leagues": {"name": "all", "value": "all"}
        },
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
    """Get list of all valid team codes (flat list)."""
    data = load_autocomplete_data()
    team_codes_by_division = data.get("team_codes", {})
    all_codes = []
    for division_codes in team_codes_by_division.values():
        all_codes.extend(division_codes)
    return sorted(all_codes)


def get_league_entries() -> List[Dict[str, str]]:
    """Get league codes/names from autocomplete data."""
    data = load_autocomplete_data()
    codes_map = data.get("leagues_codes", {}) or {}
    full_names = data.get("leagues_full_names", {}) or {}

    entries: List[Dict[str, str]] = []
    seen_codes = set()

    for key, raw_code in codes_map.items():
        code = (raw_code or key).upper()
        seen_codes.add(code)
        entries.append({
            "code": code,
            "name": full_names.get(key) or full_names.get(code) or code,
        })

    for key, name in full_names.items():
        code = codes_map.get(key) or key
        normalized = (code or key).upper()
        if normalized in seen_codes:
            continue
        seen_codes.add(normalized)
        entries.append({"code": normalized, "name": name})

    return entries
