"""Shared rules for reporting a game (``/report-score``, ``/report-stats``, ``/report-stats-bulk``).

* Regular season: both teams must be in the same division this season, and the pair may play at
  most ``games_per_matchup`` games (a division rule).
* Postseason: the game is attached to the open playoff series between the two teams. A series
  takes games until someone reaches the wins it needs.
* Any other phase: no games.

The database enforces the same rules (``validate_match``); checking here first gives owners a
clear message before they type in a whole stat sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from db import queries
from db.league_repository import LeagueRepository, LeagueRuleError, run
from services.standings import refresh_after_match
from utils import board_events
from utils.league_cache import LeagueCache

STAGE_LABELS = {
    "regular": "Regular season",
    "championship": "Championship playoff",
    "promotion": "Promotion playoff",
    "relegation": "Relegation playoff",
    "promo_rel": "Promotion/relegation playoff",
}


@dataclass
class MatchContext:
    season: Dict[str, Any]
    stage: str
    division_id: Optional[str]
    series: Optional[Dict[str, Any]] = None
    games_limit: Optional[int] = None
    played: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_playoff(self) -> bool:
        return self.series is not None

    @property
    def at_limit(self) -> bool:
        return bool(self.games_limit) and len(self.played) >= self.games_limit

    def match_fields(self) -> Dict[str, Any]:
        """Columns that place the game (the database fills stage and division for series games)."""
        if self.series:
            return {"series_id": self.series["id"], "division_id": self.division_id}
        return {"division_id": self.division_id}

    def summary_lines(self, team1_id: str, team1_name: str, team2_name: str) -> List[str]:
        lines = [f"**{team1_name}** vs **{team2_name}** · {STAGE_LABELS.get(self.stage, self.stage)}"]
        if self.series:
            s = self.series
            first_is_team1 = s.get("team1_id") == team1_id
            wins1, wins2 = (s.get("team1_wins", 0), s.get("team2_wins", 0)) if first_is_team1 else (s.get("team2_wins", 0), s.get("team1_wins", 0))
            lines.append(f"Series: **{wins1}–{wins2}** · best of {s.get('best_of', 1)} (first to {s.get('best_of', 1) // 2 + 1})")
        else:
            lines.append(f"Limit: **{self.games_limit or '—'}** · Played: **{len(self.played)}**")
        for match in self.played[:10]:
            score1, score2 = match.get("team1_score", 0), match.get("team2_score", 0)
            if match.get("team1_id") != team1_id:
                score1, score2 = score2, score1
            lines.append(f"• {score1} - {score2}")
        return lines

    def ensure_open(self) -> None:
        if self.at_limit:
            raise LeagueRuleError("MATCH_LIMIT", f"All {self.games_limit} games between these teams are already recorded.")


async def resolve_match_context(supabase: Any, team1_id: str, team2_id: str) -> MatchContext:
    """Where a game between two teams would count right now. Raises LeagueRuleError when it can't."""
    await LeagueCache.ensure_ready()
    season = LeagueCache.active_season()
    if not season:
        raise LeagueRuleError("SEASON_WRONG_PHASE", "There is no active season.")
    phase = season.get("phase")

    if phase == "regular":
        await LeagueCache.load_season_teams(season["id"])
        division_a = LeagueCache.team_division(team1_id, season["id"])
        division_b = LeagueCache.team_division(team2_id, season["id"])
        if not division_a or not division_b:
            raise LeagueRuleError("MATCH_TEAM_NOT_IN_SEASON")
        if division_a["id"] != division_b["id"]:
            raise LeagueRuleError("MATCH_DIVISION_MISMATCH")
        played = await queries.get_matches_between_teams(supabase, season["id"], team1_id, team2_id)
        return MatchContext(
            season=season,
            stage="regular",
            division_id=division_a["id"],
            games_limit=LeagueCache.settings(division_a["id"]).games_per_matchup,
            played=played,
        )

    if phase == "postseason":
        repo = LeagueRepository(supabase)
        series = await repo.find_ready_series(season["id"], team1_id, team2_id)
        if not series:
            raise LeagueRuleError("MATCH_NO_SERIES")
        bracket = series.get("brackets") or {}
        stage = bracket.get("kind") or "championship"
        played = [m for m in await repo.list_series_matches(series["id"]) if m.get("status") == "final"]
        return MatchContext(
            season=season,
            stage=stage,
            division_id=None if stage == "promo_rel" else bracket.get("division_id"),
            series=series,
            played=played,
        )

    raise LeagueRuleError("MATCH_WRONG_PHASE")


async def record_match(supabase: Any, match_entry: Any) -> tuple:
    """Check and insert a reported game. Returns (match row, context). Raises LeagueRuleError."""
    context = await resolve_match_context(supabase, match_entry.team1.team_id, match_entry.team2.team_id)
    context.ensure_open()
    payload = match_entry.to_supabase_match(context.season["id"], context.division_id)
    payload.update(context.match_fields())
    response = await run(supabase.table("matches").insert(payload))
    if not response.data:
        raise LeagueRuleError("MATCH_INVALID", "The game could not be saved. Please try again.")
    match = response.data[0]
    if context.stage == "regular":
        await refresh_after_match(supabase, context.season["id"], match.get("division_id") or context.division_id)
    board_events.league_changed()
    return match, context


async def void_match(supabase: Any, match: Dict[str, Any], voided_by: int) -> None:
    """Void a game (playoff series recount themselves) and refresh the division's standings."""
    repo = LeagueRepository(supabase)
    await repo.void_match(match["id"], voided_by)
    if match.get("stage", "regular") == "regular":
        await refresh_after_match(supabase, match.get("season_id"), match.get("division_id"))
    board_events.league_changed()
