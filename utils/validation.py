"""
Validation utilities for stat registration and database operations
Pure functions - no circular imports
"""

import logging
import re
import unicodedata
from typing import Tuple

logger = logging.getLogger(__name__)


def validate_match_scores(team1_score: int, team2_score: int) -> Tuple[bool, str]:
    """
    Validate match scores.
    
    Rules:
    - Both scores must be > 0
    - Scores cannot be equal (no draws)
    
    Returns:
        (is_valid, error_message)
    """
    if not isinstance(team1_score, int) or not isinstance(team2_score, int):
        return False, "Scores must be integers"

    if team1_score <= 0 or team2_score <= 0:
        return False, "Both scores must be greater than 0"

    if team1_score == team2_score:
        return False, "Scores cannot be equal (no draws allowed)"

    return True, ""


def validate_player_stats(stats_dict: dict) -> Tuple[bool, str]:
    """
    Validate player stats dictionary.
    
    All stats must be non-negative integers.
    
    Returns:
        (is_valid, error_message)
    """
    required_fields = [
        'goals', 'own_goals', 'points', 'assists',
        'saves', 'blocks', 'powerup_plays', 'powerups_used'
    ]

    for field in required_fields:
        if field not in stats_dict:
            return False, f"Missing required field: {field}"

        value = stats_dict[field]
        if not isinstance(value, int):
            return False, f"{field} must be an integer"

        if value < 0:
            return False, f"{field} cannot be negative"

    return True, ""


def validate_team_selection(team1_code: str, team2_code: str) -> Tuple[bool, str]:
    """Validate team selection"""
    if team1_code == team2_code:
        return False, "Teams must be different"

    if not team1_code or not team2_code:
        return False, "Both teams must be selected"

    return True, ""


# Characters that let a name break out of markdown or forge a mention: control characters, backticks,
# angle brackets (<@id>, <@&role>, <#channel>) and the two broadcast mentions.
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f`<>]|@everyone|@here", re.IGNORECASE)


def clean_name(text: str, label: str = "Name", max_len: int = 64) -> str:
    """Trimmed name safe to show anywhere. Raises ValueError (message safe for users) when it is not."""
    raw = (text or "").strip()
    if _UNSAFE_NAME.search(raw):
        raise ValueError(f"{label} cannot contain backticks, < >, line breaks, @everyone or @here.")
    name = " ".join(raw.split())
    if not name:
        raise ValueError(f"{label} cannot be empty.")
    if len(name) > max_len:
        raise ValueError(f"{label} can be at most {max_len} characters.")
    return name


def sanitize_player_name(text: str, max_len: int = 64) -> str:
    """Discord display name made safe for storage: unsafe characters removed rather than refused."""
    name = unicodedata.normalize("NFC", text or "")
    name = _UNSAFE_NAME.sub("", name)
    name = " ".join(name.split())[:max_len].strip()
    return name or "Unknown player"
