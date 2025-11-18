"""
Autocomplete data loader and helper utilities.
Static data for autocomplete suggestions that rarely change.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_AUTOCOMPLETE_DATA: Optional[Dict[str, Any]] = None


def load_autocomplete_data() -> Dict[str, Any]:
    """Load autocomplete data from JSON file (cached in memory)."""
    global _AUTOCOMPLETE_DATA
    
    if _AUTOCOMPLETE_DATA is not None:
        return _AUTOCOMPLETE_DATA
    
    try:
        data_path = Path(__file__).parent.parent / "data" / "autocomplete_data.json"
        with open(data_path, "r") as f:
            _AUTOCOMPLETE_DATA = json.load(f)
        logger.info(f"Loaded autocomplete data from {data_path}")
        return _AUTOCOMPLETE_DATA
    except FileNotFoundError:
        logger.error("autocomplete_data.json not found. Using defaults.")
        _AUTOCOMPLETE_DATA = get_default_autocomplete_data()
        return _AUTOCOMPLETE_DATA
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse autocomplete_data.json: {e}")
        _AUTOCOMPLETE_DATA = get_default_autocomplete_data()
        return _AUTOCOMPLETE_DATA


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

    special_all = (data.get("special_options", {}) or {}).get("all_leagues")
    if special_all:
        entries.append({
            "code": special_all.get("value", "all").lower(),
            "name": special_all.get("description") or special_all.get("name") or "All Leagues",
        })

    return entries
