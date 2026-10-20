"""What finished playoff brackets decide: the champion and playoff promotions/relegations.

Automatic (direct) moves come from the final standings zones (``services.league.direct_movements``);
this module adds the playoff results on top.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION
from services.league import Movement


def _roots(series: List[Dict[str, Any]], feed_key: str) -> List[Dict[str, Any]]:
    """Series whose result doesn't feed another series (the last series of each sub-bracket)."""
    return sorted((s for s in series if not s.get(feed_key)), key=lambda s: s.get("position") or 0)


def outcome_from_brackets(brackets: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Movement]]:
    """(champion team id, playoff movements) from brackets with nested ``series`` and ``bracket_entries``."""
    from utils.league_cache import LeagueCache

    champion: Optional[str] = None
    movements: List[Movement] = []
    for bracket in brackets:
        kind = bracket.get("kind")
        series = bracket.get("series") or []
        division_id = bracket.get("division_id")

        if kind == KIND_CHAMPIONSHIP:
            roots = _roots(series, "winner_to")
            if roots and roots[0].get("winner_id"):
                champion = roots[0]["winner_id"]

        elif kind == KIND_PROMOTION:
            above = LeagueCache.division_above(division_id)
            for root in _roots(series, "winner_to"):
                if above and root.get("winner_id"):
                    movements.append(Movement(root["winner_id"], division_id, above["id"], "promoted_playoff"))

        elif kind == KIND_RELEGATION:
            below = LeagueCache.division_below(division_id)
            for root in _roots(series, "loser_to"):
                if below and root.get("loser_id"):
                    movements.append(Movement(root["loser_id"], division_id, below["id"], "relegated_playoff"))

        elif kind == KIND_PROMO_REL:
            lower_id = bracket.get("lower_division_id")
            source = {e["team_id"]: e.get("source_division_id") for e in bracket.get("bracket_entries") or []}
            for item in series:
                winner, loser = item.get("winner_id"), item.get("loser_id")
                if not winner or not loser:
                    continue
                if source.get(winner) == lower_id:  # the lower team won: the two swap divisions
                    movements.append(Movement(winner, lower_id, division_id, "promoted_playoff"))
                    movements.append(Movement(loser, division_id, lower_id, "relegated_playoff"))
    return champion, movements


async def playoff_outcome(repo: Any, brackets: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Movement]]:
    """Async wrapper used by the season lifecycle (brackets already include their series)."""
    return outcome_from_brackets(brackets)


def describe_bracket_status(bracket: Dict[str, Any]) -> Dict[str, int]:
    series = bracket.get("series") or []
    return {
        "total": len(series),
        "completed": sum(1 for s in series if s.get("status") == "completed"),
        "ready": sum(1 for s in series if s.get("status") == "ready"),
    }
