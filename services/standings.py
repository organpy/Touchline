"""Division standings: ranking, tiebreakers and promotion/relegation/playoff zones.

Ranking order inside a division:

1. competition points (``points_scored`` mode: total points scored; ``win_points`` mode: wins × N)
2. point difference
3. points for
4. head-to-head among the teams still level (points earned in their games against each other, then
   the point difference in those games)
5. wins
6. team name

Zones mark where a team would finish: ``champion_playoff`` (top division playoff),
``promoted`` / ``promotion_playoff`` (from the boundary with the division above) and
``relegation_playoff`` / ``relegated`` (from this division's boundary with the one below).

The pure functions here are used by the bot and its tests. ``recompute_division`` writes the result
to the ``standings`` table so the bot, the public board and the website all show the same table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.division_settings import BoundaryConfig, ChampionshipConfig, DivisionSettings

logger = logging.getLogger(__name__)

ZONE_CHAMPION_PLAYOFF = "champion_playoff"
ZONE_PROMOTED = "promoted"
ZONE_PROMOTION_PLAYOFF = "promotion_playoff"
ZONE_RELEGATION_PLAYOFF = "relegation_playoff"
ZONE_RELEGATED = "relegated"

ZONE_LABELS = {
    ZONE_CHAMPION_PLAYOFF: "Championship playoff",
    ZONE_PROMOTED: "Promotion",
    ZONE_PROMOTION_PLAYOFF: "Promotion playoff",
    ZONE_RELEGATION_PLAYOFF: "Relegation playoff",
    ZONE_RELEGATED: "Relegation",
}
ZONE_EMOJI = {
    ZONE_CHAMPION_PLAYOFF: "🏆",
    ZONE_PROMOTED: "⬆️",
    ZONE_PROMOTION_PLAYOFF: "🔼",
    ZONE_RELEGATION_PLAYOFF: "🔽",
    ZONE_RELEGATED: "⬇️",
}


@dataclass
class StandingRow:
    team_id: str
    name: str = ""
    group_id: Optional[str] = None
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    points_for: int = 0
    points_against: int = 0
    competition_points: int = 0
    rank: int = 0
    group_rank: Optional[int] = None
    zone: Optional[str] = None
    tiebreak: List[str] = field(default_factory=list)

    @property
    def point_diff(self) -> int:
        return self.points_for - self.points_against

    def to_db(self, season_id: str, division_id: str) -> Dict[str, Any]:
        return {
            "season_id": season_id,
            "team_id": self.team_id,
            "division_id": division_id,
            "group_id": self.group_id,
            "rank": self.rank,
            "group_rank": self.group_rank,
            "games_played": self.games_played,
            "wins": self.wins,
            "losses": self.losses,
            "points_for": self.points_for,
            "points_against": self.points_against,
            "point_diff": self.point_diff,
            "competition_points": self.competition_points,
            "zone": self.zone,
        }


# ---------------------------------------------------------------------------- records


def _score(match: Mapping[str, Any], team_id: str) -> Optional[Tuple[int, int]]:
    """(points for, points against) for a team in a match, or None if it didn't play."""
    a, b = str(match.get("team1_id")), str(match.get("team2_id"))
    s1, s2 = int(match.get("team1_score") or 0), int(match.get("team2_score") or 0)
    if team_id == a:
        return s1, s2
    if team_id == b:
        return s2, s1
    return None


def build_rows(
    teams: Iterable[Mapping[str, Any]],
    matches: Iterable[Mapping[str, Any]],
    settings: DivisionSettings,
) -> Dict[str, StandingRow]:
    """Records for the division's teams from its final regular-season matches.

    ``teams`` items need ``team_id`` and may have ``name`` and ``group_id``.
    """
    rows = {
        str(t["team_id"]): StandingRow(team_id=str(t["team_id"]), name=t.get("name") or "", group_id=t.get("group_id"))
        for t in teams
    }
    for match in matches:
        for team_id, row in rows.items():
            score = _score(match, team_id)
            if score is None:
                continue
            scored, conceded = score
            row.games_played += 1
            row.points_for += scored
            row.points_against += conceded
            if scored > conceded:
                row.wins += 1
            elif scored < conceded:
                row.losses += 1
    for row in rows.values():
        row.competition_points = settings.competition_points(wins=row.wins, points_for=row.points_for)
    return rows


# ---------------------------------------------------------------------------- ranking


def _primary_key(row: StandingRow) -> Tuple[int, int, int]:
    return (row.competition_points, row.point_diff, row.points_for)


def _head_to_head(
    tied: Sequence[StandingRow], matches: Sequence[Mapping[str, Any]], settings: DivisionSettings
) -> Dict[str, Tuple[int, int]]:
    """(competition points, point diff) each tied team earned in games among the tied teams only."""
    ids = {row.team_id for row in tied}
    mini = build_rows(
        ({"team_id": team_id} for team_id in ids),
        (m for m in matches if str(m.get("team1_id")) in ids and str(m.get("team2_id")) in ids),
        settings,
    )
    return {team_id: (row.competition_points, row.point_diff) for team_id, row in mini.items()}


def rank_rows(
    rows: Iterable[StandingRow],
    matches: Sequence[Mapping[str, Any]],
    settings: DivisionSettings,
) -> List[StandingRow]:
    """Sort rows best first and set ``rank`` and ``group_rank``."""
    ordered = sorted(rows, key=_primary_key, reverse=True)
    result: List[StandingRow] = []
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and _primary_key(ordered[end]) == _primary_key(ordered[index]):
            end += 1
        tied = ordered[index:end]
        if len(tied) > 1:
            h2h = _head_to_head(tied, matches, settings)
            tied = sorted(
                tied,
                key=lambda r: (-h2h[r.team_id][0], -h2h[r.team_id][1], -r.wins, r.name.lower(), r.team_id),
            )
            if any(h2h[r.team_id] != h2h[tied[0].team_id] for r in tied):
                for row in tied:
                    row.tiebreak.append("head-to-head")
        result.extend(tied)
        index = end

    group_counts: Dict[Optional[str], int] = {}
    for position, row in enumerate(result, start=1):
        row.rank = position
        if row.group_id:
            group_counts[row.group_id] = group_counts.get(row.group_id, 0) + 1
            row.group_rank = group_counts[row.group_id]
        else:
            row.group_rank = None
    return result


# ---------------------------------------------------------------------------- zones


def championship_qualifiers(rows: Sequence[StandingRow], config: ChampionshipConfig) -> List[StandingRow]:
    """Teams that make the championship playoff, best first.

    ``overall``: the top N. ``by_group``: each group gets N // groups spots (its best teams), and the
    remaining spots go to the best other teams overall.
    """
    count = min(config.teams, len(rows))
    if config.seeding != "by_group":
        return list(rows[:count])
    groups = sorted({row.group_id for row in rows if row.group_id})
    if len(groups) < 2:
        return list(rows[:count])
    per_group = count // len(groups)
    chosen = [row for row in rows if row.group_id and row.group_rank and row.group_rank <= per_group]
    chosen_ids = {row.team_id for row in chosen}
    for row in rows:
        if len(chosen) >= count:
            break
        if row.team_id not in chosen_ids:
            chosen.append(row)
            chosen_ids.add(row.team_id)
    return sorted(chosen, key=lambda r: r.rank)


def promotion_zone_sizes(boundary_above: Optional[BoundaryConfig]) -> Tuple[int, int]:
    """(direct, playoff) places at the top of a division, from the boundary with the division above."""
    if not boundary_above or not boundary_above.is_active:
        return 0, 0
    playoff = 0
    if boundary_above.playoff_swaps:
        playoff = boundary_above.playoff_swaps if boundary_above.mode == "cross" else boundary_above.lower_playoff_teams
    return boundary_above.direct_swaps, playoff


def relegation_zone_sizes(boundary_below: Optional[BoundaryConfig]) -> Tuple[int, int]:
    """(direct, playoff) places at the bottom of a division, from its boundary with the division below."""
    if not boundary_below or not boundary_below.is_active:
        return 0, 0
    playoff = 0
    if boundary_below.playoff_swaps:
        playoff = boundary_below.playoff_swaps if boundary_below.mode == "cross" else boundary_below.upper_playoff_teams
    return boundary_below.direct_swaps, playoff


def assign_zones(
    rows: Sequence[StandingRow],
    *,
    championship: Optional[ChampionshipConfig] = None,
    boundary_above: Optional[BoundaryConfig] = None,
    boundary_below: Optional[BoundaryConfig] = None,
) -> None:
    """Set ``zone`` on ranked rows. Top zones are filled first; a team never gets two zones.

    Pass ``championship`` only for the top division, ``boundary_above`` only when a division above
    exists and ``boundary_below`` only when a division below exists.
    """
    for row in rows:
        row.zone = None
    if championship and championship.enabled:
        for row in championship_qualifiers(rows, championship):
            row.zone = ZONE_CHAMPION_PLAYOFF

    direct_up, playoff_up = promotion_zone_sizes(boundary_above)
    top = [row for row in rows if row.zone is None]
    for row in top[:direct_up]:
        row.zone = ZONE_PROMOTED
    for row in top[direct_up:direct_up + playoff_up]:
        row.zone = ZONE_PROMOTION_PLAYOFF

    direct_down, playoff_down = relegation_zone_sizes(boundary_below)
    bottom = [row for row in reversed(rows) if row.zone is None]
    for row in bottom[:direct_down]:
        row.zone = ZONE_RELEGATED
    for row in bottom[direct_down:direct_down + playoff_down]:
        row.zone = ZONE_RELEGATION_PLAYOFF


def zone_conflicts(
    team_count: int,
    *,
    division_label: str = "This division",
    championship: Optional[ChampionshipConfig] = None,
    boundary_above: Optional[BoundaryConfig] = None,
    boundary_below: Optional[BoundaryConfig] = None,
) -> List[str]:
    """Problems with a division's zones for its current number of teams (empty when it fits)."""
    errors = []
    direct_up, playoff_up = promotion_zone_sizes(boundary_above)
    direct_down, playoff_down = relegation_zone_sizes(boundary_below)
    top = direct_up + playoff_up
    bottom = direct_down + playoff_down
    champ = championship.teams if championship and championship.enabled else 0

    if champ > team_count:
        errors.append(f"{division_label} has {team_count} teams but its championship playoff needs {champ}.")
    if top + bottom > team_count:
        errors.append(
            f"{division_label} has {team_count} teams but its promotion places ({top}) and relegation places "
            f"({bottom}) need {top + bottom}."
        )
    if champ and champ + bottom > team_count:
        errors.append(
            f"{division_label}: the championship playoff ({champ}) overlaps the relegation places ({bottom}) "
            f"with only {team_count} teams."
        )
    if boundary_above and boundary_above.is_active and boundary_above.direct_swaps + boundary_above.playoff_swaps > team_count:
        errors.append(f"{division_label} has fewer teams than the promotion places above it.")
    return errors


# ---------------------------------------------------------------------------- league glue


def division_context(division_id: str) -> Dict[str, Any]:
    """Settings and adjacent boundaries for a division, from LeagueCache."""
    from utils.league_cache import LeagueCache

    division = LeagueCache.division(division_id) or {}
    settings = DivisionSettings.from_division(division)
    above = LeagueCache.division_above(division_id)
    below = LeagueCache.division_below(division_id)
    return {
        "division": division,
        "settings": settings,
        "championship": settings.championship if division.get("tier") == 1 else None,
        "boundary_above": DivisionSettings.from_division(above).boundary_below if above else None,
        "boundary_below": settings.boundary_below if below else None,
    }


def compute_division(
    division_id: str,
    teams: Iterable[Mapping[str, Any]],
    matches: Sequence[Mapping[str, Any]],
) -> List[StandingRow]:
    """Ranked rows with zones for one division (pure apart from reading LeagueCache)."""
    context = division_context(division_id)
    rows = build_rows(teams, matches, context["settings"])
    ranked = rank_rows(rows.values(), matches, context["settings"])
    assign_zones(
        ranked,
        championship=context["championship"],
        boundary_above=context["boundary_above"],
        boundary_below=context["boundary_below"],
    )
    return ranked


def division_conflicts(division_id: str, team_count: Optional[int] = None, season_id: Optional[str] = None) -> List[str]:
    """``zone_conflicts`` for a division using LeagueCache settings and its team count this season."""
    from utils.league_cache import LeagueCache

    context = division_context(division_id)
    if team_count is None:
        team_count = len(LeagueCache.team_ids_in_division(division_id, season_id))
    code = (context["division"].get("code") or "This division").upper()
    return zone_conflicts(
        team_count,
        division_label=code,
        championship=context["championship"],
        boundary_above=context["boundary_above"],
        boundary_below=context["boundary_below"],
    )


def league_conflicts(season_id: Optional[str] = None) -> List[str]:
    """Zone problems across every division for the season's team counts."""
    from utils.league_cache import LeagueCache

    errors: List[str] = []
    for division in LeagueCache.divisions():
        errors.extend(division_conflicts(division["id"], season_id=season_id))
    return errors


async def recompute_division(repo: Any, season_id: str, division_id: str) -> List[StandingRow]:
    """Rank a division from its matches and write it to ``standings`` (skipped once standings are final)."""
    existing = await repo.get_standings(season_id, division_id)
    if any(row.get("final") for row in existing):
        return []
    season_teams = await repo.list_season_teams(season_id, division_id=division_id)
    teams = [
        {"team_id": row["team_id"], "group_id": row.get("group_id"), "name": (row.get("teams") or {}).get("name")}
        for row in season_teams
    ]
    matches = await repo.list_regular_matches(season_id, division_id)
    ranked = compute_division(division_id, teams, matches)
    await repo.replace_standings(season_id, division_id, [row.to_db(season_id, division_id) for row in ranked])
    return ranked


async def recompute_season(repo: Any, season_id: str) -> None:
    from utils.league_cache import LeagueCache

    for division in LeagueCache.divisions():
        await recompute_division(repo, season_id, division["id"])


async def refresh_after_match(supabase: Any, season_id: Optional[str], division_id: Optional[str]) -> None:
    """Recompute a division after a result. Never raises: a failed refresh must not fail reporting."""
    if not season_id or not division_id:
        return
    try:
        from db.league_repository import LeagueRepository

        await recompute_division(LeagueRepository(supabase), season_id, division_id)
    except Exception as exc:
        logger.error("Standings refresh failed for division %s: %s", division_id, exc, exc_info=True)
