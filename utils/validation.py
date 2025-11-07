"""
Validation utilities for stat registration and database operations
Pure functions - no circular imports
"""

import logging
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
