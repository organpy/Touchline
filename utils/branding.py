"""League branding shown in embed footers.

Touchline runs one league per Discord server. The league's display name is stored in the guild
config under ``league_name`` (Dashboard → Settings → League Name) and cached here so synchronous
code such as ``EmbedBuilder`` can read it without a database round trip.
"""

from typing import Any, Mapping, Optional

DEFAULT_LEAGUE_NAME = "Touchline"
LEAGUE_NAME_KEY = "league_name"
LEAGUE_NAME_MAX_LENGTH = 64

_league_name: Optional[str] = None


def get_league_name() -> str:
    """Return the configured league name, or the Touchline default."""
    return _league_name or DEFAULT_LEAGUE_NAME


def set_league_name(name: Optional[str]) -> None:
    """Cache a league name. Blank values fall back to the default."""
    global _league_name
    cleaned = (name or "").strip()
    _league_name = cleaned[:LEAGUE_NAME_MAX_LENGTH] or None


def apply_config(config: Optional[Mapping[str, Any]]) -> None:
    """Refresh the cached league name from a guild config dict."""
    set_league_name((config or {}).get(LEAGUE_NAME_KEY))
