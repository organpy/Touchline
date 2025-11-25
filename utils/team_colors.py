"""Team and division color/theme resolution.

Provides functions:
- get_team_color(team_code)
- get_division_color(division_code)
- get_default_color()
- resolve_color(team_code=None, division_code=None, force_color=None)

Colors are hex strings in data/team_colors.json; conversion returns int suitable for discord.Embed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT_DIR / "data" / "team_colors.json"

_loaded: bool = False
_division_colors: dict[str, str] = {}
_default_color: str = "#5865F2"
_team_colors: dict[str, Optional[str]] = {}

HEX_ALLOWED = set("0123456789abcdefABCDEF")


def _validate_hex(value: str) -> bool:
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


def _load() -> None:
    global _loaded, _division_colors, _default_color, _team_colors
    if _loaded:
        return
    try:
        with DATA_FILE.open("r", encoding="utf-8") as fh:
            raw = json.load(fh) or {}
    except FileNotFoundError:
        logger.warning("team_colors.json missing; using defaults")
        _loaded = True
        return
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse team_colors.json: %s", exc)
        _loaded = True
        return

    _division_colors = {k.upper(): v for k, v in (raw.get("division_colors") or {}).items() if isinstance(v, str)}
    _default_color = raw.get("default") or _default_color
    teams = raw.get("teams") or {}
    for code, data in teams.items():
        if not isinstance(code, str):
            continue
        primary = (data or {}).get("primary")
        if isinstance(primary, str) and _validate_hex(primary):
            _team_colors[code.lower()] = primary
        else:
            _team_colors[code.lower()] = None
    _loaded = True


def get_team_color(team_code: Optional[str]) -> Optional[int]:
    _load()
    if not team_code:
        return None
    hex_value = _team_colors.get(team_code.lower())
    if not hex_value:
        return None
    try:
        return _hex_to_int(hex_value)
    except Exception:
        return None


def get_division_color(division_code: Optional[str]) -> Optional[int]:
    _load()
    if not division_code:
        return None
    hex_value = _division_colors.get(division_code.upper())
    if not hex_value or not _validate_hex(hex_value):
        return None
    try:
        return _hex_to_int(hex_value)
    except Exception:
        return None


def get_default_color() -> int:
    _load()
    if not _validate_hex(_default_color):
        return 0x5865F2
    return _hex_to_int(_default_color)


def resolve_color(team_code: Optional[str] = None, division_code: Optional[str] = None, force_color: Optional[str] = None) -> int:
    """Resolve final color int applying priority: force_color -> team -> division -> default."""
    _load()
    if force_color:
        if _validate_hex(force_color):
            return _hex_to_int(force_color)
        # allow int already passed
        if isinstance(force_color, str) and force_color.isdigit():
            try:
                return int(force_color)
            except Exception:
                pass
    team_val = get_team_color(team_code)
    if team_val is not None:
        return team_val
    division_val = get_division_color(division_code)
    if division_val is not None:
        return division_val
    return get_default_color()


def clear_cache() -> None:
    global _loaded, _division_colors, _default_color, _team_colors
    _loaded = False
    _division_colors = {}
    _team_colors = {}

__all__ = [
    "get_team_color",
    "get_division_color",
    "get_default_color",
    "resolve_color",
    "clear_cache",
]
