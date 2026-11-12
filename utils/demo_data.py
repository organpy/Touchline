"""Made-up league data for previews and the ``/demo`` commands: nothing here reads or writes the database.

* teams, standings rows, form and played games for the image generators
* ``demo_bracket``: a real postseason plan (``services.brackets``) turned into database-shaped rows
* ``BracketSim``: plays games and forfeits on such a bracket the way ``recompute_series`` does in SQL
"""

from __future__ import annotations

import copy
import random
from typing import Any, Callable, Dict, List, Optional

from services.brackets import (
    KIND_CHAMPIONSHIP,
    KIND_PROMO_REL,
    KIND_PROMOTION,
    KIND_RELEGATION,
    championship_bracket,
    cross_bracket,
    split_bracket,
)
from services.division_settings import ChampionshipConfig
from services.standings import ZONE_CHAMPION_PLAYOFF, ZONE_PROMOTION_PLAYOFF, ZONE_RELEGATION_PLAYOFF

# (logo file stem, name): bundled logos in images/, then two without, so both looks show up
TEAMS = [
    ("frost-falcons", "Frost Falcons"), ("titans", "Titans"), ("the-herd", "The Herd"), ("ghosts", "Ghosts"),
    ("icebergs", "Icebergs"), ("the-pack", "The Pack"), ("north-central-hawks", "North Central Hawks"),
    ("blue-ballers", "Blue Ballers"), ("south-point-sharks", "South Point Sharks"), ("phantom-hornets", "Phantom Hornets"),
    ("west-bay-devils", "West Bay Devils"), ("eastside-tigers", "Eastside Tigers"), ("opo-dragons", "Opo Dragons"),
    ("the-warriors", "The Warriors"), ("northside-bobcats", "Northside Bobcats"), ("the-breeze", "The Breeze"),
    ("east-bay-seagulls", "East Bay Seagulls"), ("ice-breakers", "Ice Breakers"), ("windchargers", "Windchargers"),
    ("venison-x", "Venison X"), ("no-logo-fc", "No Logo FC"),
    ("the-extraordinarily-long-named-herd", "The Extraordinarily Long Named Herd of Mighty Bison"),
]
MAX_TEAMS = len(TEAMS)
ZONES = ["champion_playoff", "champion_playoff", "promoted", None, None, None, "relegation_playoff", "relegated"]
UPPER_DIVISION = "demo-d1"
LOWER_DIVISION = "demo-d2"


def team_id(index: int) -> str:
    return f"demo-{index + 1}"


def team_map(count: int = MAX_TEAMS, offset: int = 0) -> Dict[str, Dict[str, Any]]:
    """Team id → {id, name, code} for ``count`` demo teams starting at ``offset``."""
    result = {}
    for index in range(offset, offset + count):
        code, name = TEAMS[index % MAX_TEAMS]
        result[team_id(index)] = {"id": team_id(index), "name": name, "code": code}
    return result


# ---------------------------------------------------------------------------------- standings and games
def standings_rows(count: int, with_zones: bool = True) -> List[Dict[str, Any]]:
    rows = []
    for index in range(count):
        code, name = TEAMS[index % MAX_TEAMS]
        wins = max(count - index - 1, 0)
        rows.append({
            "team_id": team_id(index), "name": name, "code": code, "rank": index + 1, "wins": wins, "losses": index,
            "point_diff": (count // 2 - index) * 9, "competition_points": wins * 3 + 2,
            "zone": ZONES[index % len(ZONES)] if with_zones else None,
        })
    return rows


def form_for(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    patterns = ["WWWLW", "WLWWW", "LWWLW", "LLLWN", "NNNNN"]
    return {row["team_id"]: list(patterns[i % len(patterns)]) for i, row in enumerate(rows)}


def played_games(team_ids: List[str], games_per_pair: int) -> List[Dict[str, Any]]:
    """Home and away games between every pair, partly played, so every cell state shows up."""
    matches = []
    for i, home in enumerate(team_ids):
        for j, away in enumerate(team_ids):
            if i == j:
                continue
            for game in range((games_per_pair + 1) // 2 if (i + j) % 2 else games_per_pair // 2 or 1):
                if (i * 7 + j * 3 + game) % 4 == 0:
                    continue  # not played yet
                home_score = 50 + (i * 11 + j * 5 + game * 3) % 30
                away_score = 50 + (i * 5 + j * 13 + game * 7) % 30
                if home_score == away_score:
                    away_score += 1
                matches.append({"team1_id": home, "team2_id": away, "team1_score": home_score,
                                "team2_score": away_score, "status": "final"})
    return matches


def match_table_teams(count: int) -> List[Dict[str, Any]]:
    return [{"id": team_id(i), "code": TEAMS[i % MAX_TEAMS][0], "name": TEAMS[i % MAX_TEAMS][1]} for i in range(count)]


def season_stats(number: int, team: str, **stats: Any) -> Dict[str, Any]:
    row = {"season_id": f"s{number}", "season_number": number, "season_name": f"S{number}", "team_name": team,
           "games_played": 10, "goals": 12, "assists": 7, "saves": 30, "blocks": 5, "points": 54}
    row.update(stats)
    return row


def career() -> List[Dict[str, Any]]:
    return [season_stats(1, "Titans", goals=14, points=52, games_played=8),
            season_stats(2, "Frost Falcons", goals=21, points=80),
            season_stats(3, "Frost Falcons")]


# ---------------------------------------------------------------------------------- brackets
BRACKET_KINDS = (KIND_CHAMPIONSHIP, KIND_PROMOTION, KIND_RELEGATION, KIND_PROMO_REL)


def demo_bracket(kind: str = KIND_CHAMPIONSHIP, teams: int = 8, spots: int = 1, best_of: int = 1) -> Dict[str, Any]:
    """A fresh bracket in database shape (``series`` and ``bracket_entries``), built by the real plan builders.

    ``teams`` is the playoff field (for promo_rel: series between the two divisions, so ``spots`` pairs).
    Series ids are the plan's keys.
    """
    if kind not in BRACKET_KINDS:
        raise ValueError(f"unknown bracket kind {kind!r}")
    teams = max(2, min(int(teams), 16))
    spots = max(1, int(spots))

    if kind == KIND_CHAMPIONSHIP:
        rows = [{"team_id": team_id(i), "rank": i + 1, "zone": ZONE_CHAMPION_PLAYOFF} for i in range(teams)]
        rounds = [best_of] if best_of > 1 else [1]
        plan = championship_bracket(UPPER_DIVISION, rows, ChampionshipConfig(enabled=True, teams=teams, best_of_by_round=rounds))
    elif kind in (KIND_PROMOTION, KIND_RELEGATION):
        spots = min(spots, teams // 2)
        zone = ZONE_PROMOTION_PLAYOFF if kind == KIND_PROMOTION else ZONE_RELEGATION_PLAYOFF
        rows = [{"team_id": team_id(i), "rank": i + 1, "zone": zone} for i in range(teams)]
        division = LOWER_DIVISION if kind == KIND_PROMOTION else UPPER_DIVISION
        plan = split_bracket(kind, division, rows, spots, best_of)
    else:
        pairs = max(1, min(spots, teams // 2, 8))
        upper = [{"team_id": team_id(i), "rank": 10 + i, "zone": ZONE_RELEGATION_PLAYOFF} for i in range(pairs)]
        lower = [{"team_id": team_id(pairs + i), "rank": 2 + i, "zone": ZONE_PROMOTION_PLAYOFF} for i in range(pairs)]
        plan = cross_bracket(UPPER_DIVISION, LOWER_DIVISION, upper, lower, pairs, best_of)
    if plan is None:
        raise ValueError("not enough teams for that bracket")

    series = []
    for spec in plan["series"]:
        row = dict(spec)
        row.update(id=spec["key"], team1_wins=0, team2_wins=0, winner_id=None, loser_id=None, forced_winner_id=None)
        series.append(row)
    bracket = {
        "id": f"demo-{kind}", "kind": kind, "division_id": plan["division_id"],
        "lower_division_id": plan.get("lower_division_id"), "spots": plan["spots"], "status": "active",
        "config": plan["config"], "bracket_entries": plan["entries"], "series": series,
    }
    for row in series:
        _refresh_status(row)
    return bracket


def _refresh_status(series: Dict[str, Any]) -> None:
    if series.get("winner_id"):
        series["status"] = "completed"
    elif series.get("team1_id") and series.get("team2_id"):
        series["status"] = "ready"
    else:
        series["status"] = "waiting"


class BracketSim:
    """Plays a demo bracket forward in memory, mirroring ``recompute_series`` / ``feed_series_slot``."""

    def __init__(self, bracket: Dict[str, Any], seed: Optional[int] = None,
                 name: Callable[[Optional[str]], str] = lambda team: team or "TBD") -> None:
        self.initial = copy.deepcopy(bracket)
        self.name = name  # team id → display name, for the log
        self.bracket = copy.deepcopy(bracket)
        self.rng = random.Random(seed)
        self.games_played = 0
        self.log: List[str] = []

    # ------------------------------------------------------------------ lookups
    @property
    def series(self) -> List[Dict[str, Any]]:
        return self.bracket["series"]

    def get(self, series_id: str) -> Dict[str, Any]:
        return next(s for s in self.series if s["id"] == series_id)

    def ready(self) -> List[Dict[str, Any]]:
        return sorted((s for s in self.series if s["status"] == "ready"), key=lambda s: (s["round"], s["position"]))

    @property
    def started(self) -> bool:
        return self.games_played > 0 or any(s.get("forced_winner_id") for s in self.series)

    @property
    def finished(self) -> bool:
        return self.bracket["status"] == "completed"

    # ------------------------------------------------------------------ actions
    def play_game(self, series_id: Optional[str] = None, upset_chance: float = 0.35) -> Optional[str]:
        """One game in a ready series (the first one by default). The better seed usually wins.

        Returns a line describing the result, or None when nothing is ready.
        """
        ready = self.ready()
        if not ready:
            return None
        series = self.get(series_id) if series_id else ready[0]
        if series["status"] != "ready":
            return None
        seed1, seed2 = series.get("team1_seed") or 99, series.get("team2_seed") or 99
        favourite = 1 if seed1 <= seed2 else 2
        side = favourite if self.rng.random() >= upset_chance else 3 - favourite
        series[f"team{side}_wins"] += 1
        self.games_played += 1
        winner_score = self.rng.randint(55, 80)
        loser_score = winner_score - self.rng.randint(1, 18)
        winner = self.name(series[f"team{side}_id"])
        loser = self.name(series[f"team{3 - side}_id"])
        line = f"{winner} beat {loser} {winner_score}–{loser_score}"
        self.log.append(line)
        self._recompute(series)
        return line

    def play_series(self, series_id: Optional[str] = None) -> int:
        """Games until one series is decided. Returns how many were played."""
        ready = self.ready()
        if not ready:
            return 0
        target = series_id or ready[0]["id"]
        played = 0
        while self.get(target)["status"] == "ready":
            self.play_game(target)
            played += 1
        return played

    def play_round(self) -> int:
        """Decide every series that is ready now. Returns how many series were decided."""
        decided = 0
        for series in self.ready():
            self.play_series(series["id"])
            decided += 1
        return decided

    def play_all(self) -> None:
        while self.ready():
            self.play_round()

    def forfeit(self, series_id: str, winner_id: Optional[str]) -> None:
        """Admin-forced winner (or None to clear it), like ``force_series_winner``."""
        series = self.get(series_id)
        series["forced_winner_id"] = winner_id
        self._recompute(series)

    def swap_seeds(self, team_a: str, team_b: str) -> bool:
        """Swap two teams' places before any game, like ``swap_bracket_seeds``. False once the bracket started."""
        if self.started or team_a == team_b:
            return False
        entries = {e["team_id"]: e for e in self.bracket["bracket_entries"]}
        if team_a not in entries or team_b not in entries:
            return False
        entries[team_a]["seed"], entries[team_b]["seed"] = entries[team_b]["seed"], entries[team_a]["seed"]
        entries[team_a]["team_id"], entries[team_b]["team_id"] = team_b, team_a
        swap = {team_a: team_b, team_b: team_a}
        for series in self.series:
            for slot in ("team1_id", "team2_id"):
                if series.get(slot) in swap:
                    series[slot] = swap[series[slot]]
        self.bracket["bracket_entries"].sort(key=lambda e: e["seed"])
        return True

    def reset(self) -> None:
        self.bracket = copy.deepcopy(self.initial)
        self.games_played = 0
        self.log.clear()

    # ------------------------------------------------------------------ progression
    def _recompute(self, series: Dict[str, Any]) -> None:
        needed = (series.get("best_of") or 1) // 2 + 1
        t1, t2 = series.get("team1_id"), series.get("team2_id")
        forced = series.get("forced_winner_id")
        winner = loser = None
        if forced and forced in (t1, t2):
            winner, loser = forced, t2 if forced == t1 else t1
        elif t1 and t2 and series["team1_wins"] >= needed:
            winner, loser = t1, t2
        elif t1 and t2 and series["team2_wins"] >= needed:
            winner, loser = t2, t1
        series["winner_id"], series["loser_id"] = winner, loser
        _refresh_status(series)
        seed_of = {t1: series.get("team1_seed"), t2: series.get("team2_seed")}
        for team, link, slot in ((winner, "winner_to", "winner_slot"), (loser, "loser_to", "loser_slot")):
            if series.get(link):
                target = self.get(series[link])
                target[f"team{series[slot]}_id"] = team
                target[f"team{series[slot]}_seed"] = seed_of.get(team) if team else None
                self._recompute(target)
        self.bracket["status"] = "completed" if all(s["status"] == "completed" for s in self.series) else "active"
