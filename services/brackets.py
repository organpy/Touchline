"""Postseason plan: the brackets created when the regular season ends.

* **Championship** (top division): a seeded single-elimination bracket. Top seeds get byes when the
  field isn't a power of two; best-of is set per round.
* **Promotion** (a division with a ``separate`` boundary above it): winners advance. The field is
  split into one sub-bracket per promotion spot (seeds snaked across them) and each sub-bracket's
  winner is promoted. Top seeds get byes.
* **Relegation** (a division with a ``separate`` boundary below it): losers advance. One sub-bracket
  per relegation spot; each sub-bracket's last loser is relegated. Sizes are spots × 2^r, so there
  are no byes (a bye would push a team towards relegation without a game).
* **Promotion/relegation** (``cross`` boundary): head-to-head series. The best team of the upper
  division's playoff places meets the worst of the lower division's, and so on; winners take the
  upper division's places.

``build_postseason_plan`` returns the JSON the ``create_postseason`` database function expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from services.division_settings import ChampionshipConfig, DivisionSettings, rounds_needed
from services.standings import (
    ZONE_CHAMPION_PLAYOFF,
    ZONE_PROMOTION_PLAYOFF,
    ZONE_RELEGATION_PLAYOFF,
)

KIND_CHAMPIONSHIP = "championship"
KIND_PROMOTION = "promotion"
KIND_RELEGATION = "relegation"
KIND_PROMO_REL = "promo_rel"


@dataclass
class Seeded:
    """A team entering a bracket."""

    team_id: str
    seed: int
    source_division_id: str
    group_id: Optional[str] = None


@dataclass
class SeriesSpec:
    key: str
    round: int
    position: int = 0
    team1: Optional[Seeded] = None
    team2: Optional[Seeded] = None
    best_of: int = 1
    # where this series' winner/loser goes: (key, slot)
    winner_to: Optional[tuple] = None
    loser_to: Optional[tuple] = None
    children: List["SeriesSpec"] = field(default_factory=list)

    def to_plan(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "round": self.round,
            "position": self.position,
            "team1_id": self.team1.team_id if self.team1 else None,
            "team2_id": self.team2.team_id if self.team2 else None,
            "team1_seed": self.team1.seed if self.team1 else None,
            "team2_seed": self.team2.seed if self.team2 else None,
            "best_of": self.best_of,
            "winner_to": self.winner_to[0] if self.winner_to else None,
            "winner_slot": self.winner_to[1] if self.winner_to else None,
            "loser_to": self.loser_to[0] if self.loser_to else None,
            "loser_slot": self.loser_to[1] if self.loser_to else None,
        }


Node = Union[Seeded, SeriesSpec, None]


def standard_order(size: int) -> List[int]:
    """Seed positions for a bracket of ``size`` (a power of two): 1 v size, then the classic layout.

    ``standard_order(8)`` → ``[1, 8, 4, 5, 2, 7, 3, 6]`` (pairs are first-round matchups).
    """
    order = [1]
    while len(order) < size:
        length = len(order) * 2
        order = [seed for s in order for seed in (s, length + 1 - s)]
    return order


def build_tree(
    teams: Sequence[Seeded],
    rounds: int,
    *,
    key_prefix: str,
    best_of: Callable[[int], int],
    advance: str = "winner",
    series_out: List[SeriesSpec],
) -> Node:
    """Single-elimination tree of ``2**rounds`` slots for ``teams`` (best first).

    Missing teams become byes: a slot with one team passes it up a round; a slot with none
    disappears. ``advance`` is "winner" (normal) or "loser" (relegation brackets). Created series are
    appended to ``series_out``; the root node is returned (a series, or a lone team).
    """
    size = 2 ** rounds
    by_local_seed = {index + 1: team for index, team in enumerate(teams)}
    slots: List[Node] = [by_local_seed.get(seed) for seed in standard_order(size)]

    def combine(nodes: List[Node], round_number: int, path: str) -> Node:
        if len(nodes) == 1:
            return nodes[0]
        half = len(nodes) // 2
        left = combine(nodes[:half], round_number - 1, path + "a")
        right = combine(nodes[half:], round_number - 1, path + "b")
        if left is None or right is None:
            return left if right is None else right
        spec = SeriesSpec(key=f"{key_prefix}:{path or 'root'}", round=round_number, best_of=best_of(round_number))
        for slot, child in ((1, left), (2, right)):
            if isinstance(child, SeriesSpec):
                target = (spec.key, slot)
                if advance == "winner":
                    child.winner_to = target
                else:
                    child.loser_to = target
                spec.children.append(child)
            elif slot == 1:
                spec.team1 = child
            else:
                spec.team2 = child
        series_out.append(spec)
        return spec

    return combine(slots, rounds, "")


def number_positions(series: List[SeriesSpec]) -> None:
    """Positions 0..n within each round, in bracket order (top to bottom)."""
    by_round: Dict[int, List[SeriesSpec]] = {}
    for spec in sorted(series, key=lambda s: s.key):
        by_round.setdefault(spec.round, []).append(spec)
    for specs in by_round.values():
        for position, spec in enumerate(specs):
            spec.position = position


def snake(items: Sequence[Any], buckets: int) -> List[List[Any]]:
    """Distribute best-first items over buckets 1..n, n..1, 1..n…"""
    result: List[List[Any]] = [[] for _ in range(buckets)]
    for index, item in enumerate(items):
        lap, offset = divmod(index, buckets)
        result[offset if lap % 2 == 0 else buckets - 1 - offset].append(item)
    return result


def _bracket(kind: str, division_id: str, spots: int, entries: List[Seeded], series: List[SeriesSpec],
             config: Dict[str, Any], lower_division_id: Optional[str] = None) -> Dict[str, Any]:
    number_positions(series)
    return {
        "kind": kind,
        "division_id": division_id,
        "lower_division_id": lower_division_id,
        "spots": spots,
        "config": config,
        "entries": [{"team_id": e.team_id, "seed": e.seed, "source_division_id": e.source_division_id} for e in entries],
        "series": [s.to_plan() for s in sorted(series, key=lambda s: (s.round, s.position))],
    }


# ---------------------------------------------------------------------------- bracket builders


def championship_bracket(division_id: str, rows: Sequence[Dict[str, Any]], config: ChampionshipConfig) -> Optional[Dict[str, Any]]:
    """Seeded knockout for the teams in the championship zone (rank order, or group winners first)."""
    qualified = [r for r in rows if r.get("zone") == ZONE_CHAMPION_PLAYOFF]
    if len(qualified) < 2:
        return None
    if config.seeding == "by_group" and any(r.get("group_id") for r in qualified):
        qualified = sorted(qualified, key=lambda r: (r.get("group_rank") or 99, r["rank"]))
    else:
        qualified = sorted(qualified, key=lambda r: r["rank"])
    entries = [Seeded(r["team_id"], seed, division_id, r.get("group_id")) for seed, r in enumerate(qualified, start=1)]
    series: List[SeriesSpec] = []
    build_tree(entries, rounds_needed(len(entries), 1), key_prefix=f"champ:{division_id}",
               best_of=config.best_of, series_out=series)
    return _bracket(KIND_CHAMPIONSHIP, division_id, 1, entries, series, {
        "seeding": config.seeding, "best_of_by_round": list(config.best_of_by_round),
    })


def split_bracket(
    kind: str,
    division_id: str,
    rows: Sequence[Dict[str, Any]],
    spots: int,
    best_of: int,
) -> Optional[Dict[str, Any]]:
    """Promotion (winners advance) or relegation (losers advance) bracket with one sub-bracket per spot."""
    zone = ZONE_PROMOTION_PLAYOFF if kind == KIND_PROMOTION else ZONE_RELEGATION_PLAYOFF
    playoff_rows = sorted((r for r in rows if r.get("zone") == zone), key=lambda r: r["rank"])
    if len(playoff_rows) < 2 * spots:
        return None
    entries = [Seeded(r["team_id"], seed, division_id) for seed, r in enumerate(playoff_rows, start=1)]
    rounds = rounds_needed(len(entries), spots)
    series: List[SeriesSpec] = []
    advance = "winner" if kind == KIND_PROMOTION else "loser"
    for index, group in enumerate(snake(entries, spots)):
        build_tree(group, rounds, key_prefix=f"{kind}:{division_id}:{index}", best_of=lambda _: best_of,
                   advance=advance, series_out=series)
    return _bracket(kind, division_id, spots, entries, series, {"best_of": best_of, "sub_brackets": spots})


def cross_bracket(
    upper_id: str,
    lower_id: str,
    upper_rows: Sequence[Dict[str, Any]],
    lower_rows: Sequence[Dict[str, Any]],
    spots: int,
    best_of: int,
) -> Optional[Dict[str, Any]]:
    """Head-to-head series: upper's best playoff team v lower's worst playoff team, and so on."""
    upper = sorted((r for r in upper_rows if r.get("zone") == ZONE_RELEGATION_PLAYOFF), key=lambda r: r["rank"])
    lower = sorted((r for r in lower_rows if r.get("zone") == ZONE_PROMOTION_PLAYOFF), key=lambda r: r["rank"])
    pairs = min(spots, len(upper), len(lower))
    if not pairs:
        return None
    upper, lower = upper[:pairs], lower[:pairs]
    upper_entries = [Seeded(r["team_id"], seed, upper_id) for seed, r in enumerate(upper, start=1)]
    lower_entries = [Seeded(r["team_id"], pairs + seed, lower_id) for seed, r in enumerate(lower, start=1)]
    series = [
        SeriesSpec(
            key=f"promo_rel:{upper_id}:{index}",
            round=1,
            team1=upper_entries[index],
            team2=lower_entries[pairs - 1 - index],
            best_of=best_of,
        )
        for index in range(pairs)
    ]
    return _bracket(KIND_PROMO_REL, upper_id, pairs, upper_entries + lower_entries, series,
                    {"best_of": best_of}, lower_division_id=lower_id)


# ---------------------------------------------------------------------------- whole league


def build_postseason_plan(season_id: str, standings_by_division: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Every bracket the league's settings call for, from final standings (rows need rank/zone)."""
    from utils.league_cache import LeagueCache

    brackets: List[Dict[str, Any]] = []
    divisions = LeagueCache.divisions()
    for division in divisions:
        division_id = division["id"]
        rows = standings_by_division.get(division_id) or []
        settings = DivisionSettings.from_division(division)

        if division.get("tier") == 1 and settings.championship.enabled:
            bracket = championship_bracket(division_id, rows, settings.championship)
            if bracket:
                brackets.append(bracket)

        below = LeagueCache.division_below(division_id)
        boundary = settings.boundary_below
        if not below or not boundary.playoff_swaps:
            continue
        lower_rows = standings_by_division.get(below["id"]) or []
        if boundary.mode == "cross":
            bracket = cross_bracket(division_id, below["id"], rows, lower_rows, boundary.playoff_swaps, boundary.best_of)
            if bracket:
                brackets.append(bracket)
        else:
            for kind, target_id, target_rows in (
                (KIND_RELEGATION, division_id, rows),
                (KIND_PROMOTION, below["id"], lower_rows),
            ):
                bracket = split_bracket(kind, target_id, target_rows, boundary.playoff_swaps, boundary.best_of)
                if bracket:
                    brackets.append(bracket)
    return {"brackets": brackets}
