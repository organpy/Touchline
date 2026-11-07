"""Season lifecycle: setup → regular season → postseason → offseason → next season.

Every step is driven from the dashboard (Season page). Each one checks the league first and raises
``LeagueRuleError`` with a readable message when something has to be fixed. The state changes that
touch many rows run inside database functions (``create_postseason``, ``finalize_season``,
``start_next_season``) so they are all-or-nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from db.league_repository import LeagueRuleError
from services.division_settings import DivisionSettings
from services.standings import (
    ZONE_PROMOTED,
    ZONE_RELEGATED,
    league_conflicts,
    recompute_division,
)
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)

MIN_TEAMS_PER_DIVISION = 2

NEXT_STEP = {
    "setup": "Start regular season",
    "regular": "End regular season",
    "postseason": "Finish postseason",
    "offseason": "Start next season",
}


@dataclass
class Readiness:
    """What blocks a step (``problems``) and what is worth knowing first (``warnings``)."""

    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class Movement:
    team_id: str
    from_division_id: str
    to_division_id: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "team_id": self.team_id,
            "from_division_id": self.from_division_id,
            "to_division_id": self.to_division_id,
            "reason": self.reason,
        }


def _require_phase(season: Optional[Dict[str, Any]], phase: str) -> Dict[str, Any]:
    if not season:
        raise LeagueRuleError("SEASON_WRONG_PHASE", "There is no active season.")
    if season.get("phase") != phase:
        raise LeagueRuleError("SEASON_WRONG_PHASE")
    return season


def _raise_if_blocked(readiness: Readiness) -> None:
    if not readiness.ok:
        raise LeagueRuleError("SEASON_NOT_READY", " ".join(readiness.problems))


# ---------------------------------------------------------------------------- setup → regular


def regular_season_readiness(season_id: str, all_team_ids: List[str]) -> Readiness:
    """Checks before the regular season starts (uses LeagueCache; load the season's teams first)."""
    result = Readiness()
    divisions = LeagueCache.divisions()
    if not divisions:
        result.problems.append("Add at least one division first.")
        return result

    for division in divisions:
        code = (division.get("code") or "?").upper()
        count = len(LeagueCache.team_ids_in_division(division["id"], season_id))
        if count < MIN_TEAMS_PER_DIVISION:
            result.problems.append(f"{code} needs at least {MIN_TEAMS_PER_DIVISION} teams (has {count}).")
        for error in DivisionSettings.from_division(division).validate():
            result.problems.append(f"{code}: {error}")
    result.problems.extend(league_conflicts(season_id))

    assigned = set(LeagueCache.assigned_team_ids(season_id))
    sitting_out = [team_id for team_id in all_team_ids if team_id not in assigned]
    if sitting_out:
        result.warnings.append(f"{len(sitting_out)} team(s) have no division and will sit this season out.")
    return result


async def start_regular_season(repo: Any, season: Dict[str, Any], all_team_ids: List[str]) -> None:
    season = _require_phase(season, "setup")
    await LeagueCache.load_season_teams(season["id"])
    _raise_if_blocked(regular_season_readiness(season["id"], all_team_ids))
    await repo.set_season_phase(season["id"], "regular")
    for division in LeagueCache.divisions():
        await recompute_division(repo, season["id"], division["id"])


# ---------------------------------------------------------------------------- regular → postseason

PlanBuilder = Callable[[str, Dict[str, List[Dict[str, Any]]]], Dict[str, Any]]


def empty_plan(season_id: str, standings_by_division: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """No playoffs: standings decide everything."""
    return {"brackets": []}


async def final_standings(repo: Any, season_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Recompute and return every division's standings, keyed by division id (rank order)."""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for division in LeagueCache.divisions():
        await recompute_division(repo, season_id, division["id"])
        result[division["id"]] = sorted(await repo.get_standings(season_id, division["id"]), key=lambda r: r["rank"])
    return result


async def end_regular_season(repo: Any, season: Dict[str, Any], plan_builder: Optional[PlanBuilder] = None) -> int:
    """Lock the standings, create the playoff brackets and enter the postseason. Returns bracket count."""
    season = _require_phase(season, "regular")
    standings = await final_standings(repo, season["id"])
    plan = (plan_builder or default_plan_builder())(season["id"], standings)
    return await repo.create_postseason(season["id"], plan)


def default_plan_builder() -> PlanBuilder:
    """The postseason plan for the league's settings (playoffs are added in services.brackets)."""
    try:
        from services.brackets import build_postseason_plan
    except ImportError:  # pragma: no cover - brackets not installed
        return empty_plan
    return build_postseason_plan


# ---------------------------------------------------------------------------- postseason → offseason


def direct_movements(standings_by_division: Dict[str, List[Dict[str, Any]]]) -> List[Movement]:
    """Automatic promotions and relegations from the final standings zones."""
    movements: List[Movement] = []
    for division_id, rows in standings_by_division.items():
        above = LeagueCache.division_above(division_id)
        below = LeagueCache.division_below(division_id)
        for row in rows:
            if row.get("zone") == ZONE_PROMOTED and above:
                movements.append(Movement(row["team_id"], division_id, above["id"], "promoted"))
            elif row.get("zone") == ZONE_RELEGATED and below:
                movements.append(Movement(row["team_id"], division_id, below["id"], "relegated"))
    return movements


def standings_champion(standings_by_division: Dict[str, List[Dict[str, Any]]]) -> Optional[str]:
    """Top team of the top division."""
    top = LeagueCache.top_division()
    rows = standings_by_division.get(top["id"]) if top else None
    return rows[0]["team_id"] if rows else None


@dataclass
class SeasonOutcome:
    champion_team_id: Optional[str]
    movements: List[Movement]
    unfinished_brackets: int = 0


async def season_outcome(repo: Any, season_id: str) -> SeasonOutcome:
    """Champion and promotions/relegations the season produced (playoffs included when present)."""
    standings: Dict[str, List[Dict[str, Any]]] = {}
    for division in LeagueCache.divisions():
        standings[division["id"]] = sorted(await repo.get_standings(season_id, division["id"]), key=lambda r: r["rank"])
    brackets = await repo.list_brackets(season_id)
    unfinished = sum(1 for b in brackets if b.get("status") != "completed")

    try:
        from services.movements import playoff_outcome
    except ImportError:  # pragma: no cover - brackets not installed
        playoff_outcome = None

    champion = standings_champion(standings)
    movements = direct_movements(standings)
    if playoff_outcome and brackets:
        playoff_champion, playoff_moves = await playoff_outcome(repo, brackets)
        champion = playoff_champion or champion
        movements.extend(playoff_moves)
    return SeasonOutcome(champion, movements, unfinished)


async def finish_postseason(repo: Any, season: Dict[str, Any]) -> SeasonOutcome:
    season = _require_phase(season, "postseason")
    outcome = await season_outcome(repo, season["id"])
    if outcome.unfinished_brackets:
        raise LeagueRuleError("BRACKETS_IN_PROGRESS")
    await repo.finalize_season(season["id"], outcome.champion_team_id, [m.to_dict() for m in outcome.movements])
    return outcome


# ---------------------------------------------------------------------------- offseason → next season


def suggested_next_name(season: Dict[str, Any]) -> str:
    """"Season 3" → "Season 4"; anything else → "Season <number + 1>"."""
    number = int(season.get("number") or 0) + 1
    name = (season.get("name") or "").strip()
    head, _, tail = name.rpartition(" ")
    if head and tail.isdigit():
        return f"{head} {int(tail) + 1}"
    return f"Season {number}"


async def start_next_season(repo: Any, season: Dict[str, Any], name: str) -> str:
    season = _require_phase(season, "offseason")
    name = (name or "").strip() or suggested_next_name(season)
    return await repo.start_next_season(season["id"], name)
