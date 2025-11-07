"""
Utility functions for stat calculations
Pure functions - no circular imports
"""

from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


def calculate_powerup_efficiency(powerup_plays: int, powerups_used: int) -> float:
    """Calculate powerup efficiency percentage"""
    if powerups_used <= 0:
        return 0.0
    return round((powerup_plays / powerups_used) * 100, 2)


def calculate_season_averages(stat_list: List[Dict]) -> Dict[str, float]:
    """
    Calculate per-game averages from a list of player stats.
    
    Args:
        stat_list: List of player_stats dictionaries
    
    Returns:
        Dict with keys: games_played, ppg, apg, bpg, gpg, spg, opg, pp_efficiency
    """
    if not stat_list:
        return {
            'games_played': 0,
            'ppg': 0.0,
            'apg': 0.0,
            'bpg': 0.0,
            'gpg': 0.0,
            'spg': 0.0,
            'opg': 0.0,
            'pp_efficiency': 0.0
        }

    gp = len(stat_list)

    total_points = sum(s.get('points', 0) for s in stat_list)
    total_assists = sum(s.get('assists', 0) for s in stat_list)
    total_blocks = sum(s.get('blocks', 0) for s in stat_list)
    total_goals = sum(s.get('goals', 0) for s in stat_list)
    total_saves = sum(s.get('saves', 0) for s in stat_list)
    total_own_goals = sum(s.get('own_goals', 0) for s in stat_list)
    total_pp_plays = sum(s.get('powerup_plays', 0) for s in stat_list)
    total_pp_used = sum(s.get('powerups_used', 0) for s in stat_list)

    return {
        'games_played': gp,
        'ppg': round(total_points / gp, 2),
        'apg': round(total_assists / gp, 2),
        'bpg': round(total_blocks / gp, 2),
        'gpg': round(total_goals / gp, 2),
        'spg': round(total_saves / gp, 2),
        'opg': round(total_own_goals / gp, 2),
        'pp_efficiency': calculate_powerup_efficiency(total_pp_plays, total_pp_used)
    }


def calculate_season_totals(stat_list: List[Dict]) -> Dict[str, int]:
    """Calculate season totals from a list of player stats"""
    if not stat_list:
        return {
            'games_played': 0,
            'total_points': 0,
            'total_assists': 0,
            'total_blocks': 0,
            'total_goals': 0,
            'total_saves': 0,
            'total_own_goals': 0,
            'total_pp_plays': 0,
            'total_pp_used': 0
        }

    return {
        'games_played': len(stat_list),
        'total_points': sum(s.get('points', 0) for s in stat_list),
        'total_assists': sum(s.get('assists', 0) for s in stat_list),
        'total_blocks': sum(s.get('blocks', 0) for s in stat_list),
        'total_goals': sum(s.get('goals', 0) for s in stat_list),
        'total_saves': sum(s.get('saves', 0) for s in stat_list),
        'total_own_goals': sum(s.get('own_goals', 0) for s in stat_list),
        'total_pp_plays': sum(s.get('powerup_plays', 0) for s in stat_list),
        'total_pp_used': sum(s.get('powerups_used', 0) for s in stat_list)
    }
