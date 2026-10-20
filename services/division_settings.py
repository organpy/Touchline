"""Per-division rules, stored as JSON in ``divisions.settings``.

Everything an admin can configure about a division lives here: how standings are scored, how many
games each pair of teams plays, whether the division runs a draft, the championship playoff (top tier
only) and the promotion/relegation boundary to the division below.

``DivisionSettings.from_dict`` is tolerant: unknown keys are ignored and missing or malformed values
fall back to defaults, so old rows never break the bot. ``validate`` lists what an admin must fix
before saving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

SCORING_MODES = ("points_scored", "win_points")
SEEDING_MODES = ("overall", "by_group")
BOUNDARY_MODES = ("separate", "cross")

MAX_CHAMPIONSHIP_TEAMS = 16
MAX_BEST_OF = 9
MAX_SWAPS = 8
MAX_GAMES_PER_MATCHUP = 10
MAX_POINTS_PER_WIN = 10

SCORING_LABELS = {
    "points_scored": "Total points scored",
    "win_points": "Points per win",
}
BOUNDARY_MODE_LABELS = {
    "separate": "Separate brackets",
    "cross": "Cross-tier head-to-head",
}
SEEDING_LABELS = {
    "overall": "Overall standings",
    "by_group": "By group",
}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _choice(value: Any, choices: tuple, default: str) -> str:
    return value if value in choices else default


def is_power_of_two(value: int) -> bool:
    return value >= 1 and (value & (value - 1)) == 0


def rounds_needed(teams: int, spots: int) -> int:
    """Rounds an elimination bracket of ``teams`` needs to leave ``spots`` teams standing."""
    rounds = 0
    size = spots
    while size < teams:
        size *= 2
        rounds += 1
    return rounds


@dataclass
class ChampionshipConfig:
    """Seeded championship playoff for the top division."""

    enabled: bool = False
    teams: int = 4
    seeding: str = "overall"
    # Best-of per round, first round first. The last value repeats for later rounds.
    best_of_by_round: List[int] = field(default_factory=lambda: [1])

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "ChampionshipConfig":
        data = data or {}
        raw_best_of = data.get("best_of_by_round")
        if isinstance(raw_best_of, (list, tuple)) and raw_best_of:
            best_of = [_int(v, 1) for v in raw_best_of]
        else:
            best_of = [1]
        return cls(
            enabled=bool(data.get("enabled", False)),
            teams=_int(data.get("teams"), 4),
            seeding=_choice(data.get("seeding"), SEEDING_MODES, "overall"),
            best_of_by_round=best_of,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "teams": self.teams,
            "seeding": self.seeding,
            "best_of_by_round": list(self.best_of_by_round),
        }

    @property
    def rounds(self) -> int:
        return rounds_needed(self.teams, 1)

    def best_of(self, round_number: int) -> int:
        """Best-of for a 1-based round number."""
        if not self.best_of_by_round:
            return 1
        index = min(max(round_number, 1), len(self.best_of_by_round)) - 1
        return self.best_of_by_round[index]

    def validate(self) -> List[str]:
        if not self.enabled:
            return []
        errors = []
        if not 2 <= self.teams <= MAX_CHAMPIONSHIP_TEAMS:
            errors.append(f"Championship playoff needs between 2 and {MAX_CHAMPIONSHIP_TEAMS} teams.")
        if self.seeding not in SEEDING_MODES:
            errors.append("Championship seeding must be overall or by group.")
        for value in self.best_of_by_round:
            if value < 1 or value > MAX_BEST_OF or value % 2 == 0:
                errors.append(f"Best-of values must be odd numbers from 1 to {MAX_BEST_OF} (got {value}).")
                break
        return errors


@dataclass
class BoundaryConfig:
    """Promotion/relegation between this division and the one directly below it.

    ``direct_swaps`` teams go down and up automatically. ``playoff_swaps`` more spots are decided by
    playoffs: in ``separate`` mode this division runs a relegation bracket of
    ``upper_playoff_teams`` (losers advance) and the division below runs a promotion bracket of
    ``lower_playoff_teams`` (winners advance); in ``cross`` mode the bottom of this division plays the
    top of the division below head to head.
    """

    direct_swaps: int = 0
    playoff_swaps: int = 0
    mode: str = "separate"
    upper_playoff_teams: int = 0
    lower_playoff_teams: int = 0
    best_of: int = 1

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "BoundaryConfig":
        data = data or {}
        config = cls(
            direct_swaps=max(_int(data.get("direct_swaps"), 0), 0),
            playoff_swaps=max(_int(data.get("playoff_swaps"), 0), 0),
            mode=_choice(data.get("mode"), BOUNDARY_MODES, "separate"),
            upper_playoff_teams=max(_int(data.get("upper_playoff_teams"), 0), 0),
            lower_playoff_teams=max(_int(data.get("lower_playoff_teams"), 0), 0),
            best_of=_int(data.get("best_of"), 1),
        )
        return config.normalized()

    def normalized(self) -> "BoundaryConfig":
        """Make dependent fields consistent (no playoff → no playoff teams; cross → one pair per spot)."""
        if self.playoff_swaps == 0:
            self.upper_playoff_teams = 0
            self.lower_playoff_teams = 0
        elif self.mode == "cross":
            self.upper_playoff_teams = self.playoff_swaps
            self.lower_playoff_teams = self.playoff_swaps
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direct_swaps": self.direct_swaps,
            "playoff_swaps": self.playoff_swaps,
            "mode": self.mode,
            "upper_playoff_teams": self.upper_playoff_teams,
            "lower_playoff_teams": self.lower_playoff_teams,
            "best_of": self.best_of,
        }

    @property
    def total_swaps(self) -> int:
        return self.direct_swaps + self.playoff_swaps

    @property
    def is_active(self) -> bool:
        return self.total_swaps > 0

    def validate(self) -> List[str]:
        errors = []
        if self.direct_swaps > MAX_SWAPS or self.playoff_swaps > MAX_SWAPS:
            errors.append(f"At most {MAX_SWAPS} automatic and {MAX_SWAPS} playoff spots per boundary.")
        if self.best_of < 1 or self.best_of > MAX_BEST_OF or self.best_of % 2 == 0:
            errors.append(f"Pro/rel best-of must be an odd number from 1 to {MAX_BEST_OF}.")
        if self.playoff_swaps and self.mode == "separate":
            spots = self.playoff_swaps
            upper, lower = self.upper_playoff_teams, self.lower_playoff_teams
            # Relegation brackets have no byes: a bye in a losers-advance bracket would push a team
            # closer to relegation without a game.
            if upper < 2 * spots or upper % spots or not is_power_of_two(upper // spots):
                sizes = ", ".join(str(spots * 2 ** r) for r in range(1, 4))
                errors.append(
                    f"Relegation playoff size must be {sizes}… for {spots} spot(s), got {upper}."
                )
            if lower < 2 * spots:
                errors.append(
                    f"Promotion playoff needs at least {2 * spots} teams for {spots} spot(s), got {lower}."
                )
        return errors


@dataclass
class DivisionSettings:
    scoring_mode: str = "points_scored"
    points_per_win: int = 3
    games_per_matchup: int = 2
    picks_enabled: bool = False
    championship: ChampionshipConfig = field(default_factory=ChampionshipConfig)
    boundary_below: BoundaryConfig = field(default_factory=BoundaryConfig)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "DivisionSettings":
        data = data or {}
        return cls(
            scoring_mode=_choice(data.get("scoring_mode"), SCORING_MODES, "points_scored"),
            points_per_win=_int(data.get("points_per_win"), 3),
            games_per_matchup=_int(data.get("games_per_matchup"), 2),
            picks_enabled=bool(data.get("picks_enabled", False)),
            championship=ChampionshipConfig.from_dict(data.get("championship")),
            boundary_below=BoundaryConfig.from_dict(data.get("boundary_below")),
        )

    @classmethod
    def from_division(cls, division: Optional[Mapping[str, Any]]) -> "DivisionSettings":
        return cls.from_dict((division or {}).get("settings"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scoring_mode": self.scoring_mode,
            "points_per_win": self.points_per_win,
            "games_per_matchup": self.games_per_matchup,
            "picks_enabled": self.picks_enabled,
            "championship": self.championship.to_dict(),
            "boundary_below": self.boundary_below.to_dict(),
        }

    def competition_points(self, wins: int, points_for: int) -> int:
        """Standings points for a record under this division's scoring mode."""
        if self.scoring_mode == "win_points":
            return wins * self.points_per_win
        return points_for

    def validate(self) -> List[str]:
        errors = []
        if self.scoring_mode not in SCORING_MODES:
            errors.append("Scoring must be total points scored or points per win.")
        if not 1 <= self.points_per_win <= MAX_POINTS_PER_WIN:
            errors.append(f"Points per win must be between 1 and {MAX_POINTS_PER_WIN}.")
        if not 1 <= self.games_per_matchup <= MAX_GAMES_PER_MATCHUP:
            errors.append(f"Games per matchup must be between 1 and {MAX_GAMES_PER_MATCHUP}.")
        errors.extend(self.championship.validate())
        errors.extend(self.boundary_below.validate())
        return errors


def describe_rules(settings: DivisionSettings) -> str:
    """One-line summary for embeds."""
    if settings.scoring_mode == "win_points":
        scoring = f"{settings.points_per_win} pts per win"
    else:
        scoring = "points scored"
    picks = "draft on" if settings.picks_enabled else "no draft"
    return f"{scoring} · {settings.games_per_matchup} games per matchup · {picks}"


def describe_boundary(boundary: BoundaryConfig) -> str:
    if not boundary.is_active:
        return "No promotion/relegation"
    parts = []
    if boundary.direct_swaps:
        parts.append(f"{boundary.direct_swaps} automatic")
    if boundary.playoff_swaps:
        if boundary.mode == "cross":
            parts.append(f"{boundary.playoff_swaps} cross-tier playoff (bo{boundary.best_of})")
        else:
            parts.append(
                f"{boundary.playoff_swaps} via playoffs "
                f"({boundary.upper_playoff_teams}-team relegation / {boundary.lower_playoff_teams}-team promotion, "
                f"bo{boundary.best_of})"
            )
    return " + ".join(parts)


def describe_championship(config: ChampionshipConfig) -> str:
    if not config.enabled:
        return "No championship playoff"
    best_of = "/".join(f"bo{v}" for v in config.best_of_by_round)
    seeding = SEEDING_LABELS.get(config.seeding, config.seeding)
    return f"{config.teams} teams · {best_of} · seeded by {seeding.lower()}"
