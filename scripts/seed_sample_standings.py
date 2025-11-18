"""Seed sample seasons and matches so /standings has data to show.

Usage examples (run inside the virtual environment):

    python scripts/seed_sample_standings.py           # preview only
    python scripts/seed_sample_standings.py --apply   # write to Supabase
    python scripts/seed_sample_standings.py --apply --reset  # delete previous seeded matches first
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.supabase_client import get_supabase  # noqa: E402

logger = logging.getLogger("seed_sample_standings")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

TEAMS_FILE = ROOT_DIR / "teams.json"
SEED_TAG = "standings_seed_v1"


@dataclass
class LeaguePlan:
    code: str
    division_id: str
    division_name: str
    season_id: str
    season_name: str
    matches: List[Dict]
    available_teams: int


class StandingsSeeder:
    def __init__(self, teams_file: Path = TEAMS_FILE):
        load_dotenv()
        self.teams_file = teams_file
        self._supabase = None
        self._team_order = self._load_team_order()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview(self) -> List[LeaguePlan]:
        return self._build_plans(sample_only=True)

    def apply(self, reset: bool = False) -> List[LeaguePlan]:
        plans = self._build_plans(sample_only=False)
        for plan in plans:
            if not plan.matches:
                logger.warning("Skipping %s: not enough teams to seed matches", plan.code)
                continue
            if reset:
                self._delete_seeded_matches(plan.season_id)
            client = self._client()
            logger.info("Seeding %d matches for %s %s", len(plan.matches), plan.code, plan.season_name)
            client.table("matches").insert(plan.matches).execute()
        return plans

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_plans(self, sample_only: bool) -> List[LeaguePlan]:
        leagues = sorted(self._team_order.keys())
        if not leagues:
            raise RuntimeError("No leagues found in teams.json")

        client = self._client()
        division_rows = self._fetch_divisions(leagues)
        plans: List[LeaguePlan] = []

        for league_code in leagues:
            division = division_rows.get(league_code)
            if not division:
                logger.warning("No division row for %s; run seed_teams.py first", league_code)
                continue

            season = self._ensure_active_season(client, division)
            matches = []
            teams_in_division = self._fetch_teams(client, division["id"])
            ordered_codes = [meta["code"].lower() for meta in self._team_order[league_code]]
            ordered_teams = [teams_in_division[code] for code in ordered_codes if code in teams_in_division]

            if len(ordered_teams) >= 2:
                matches = self._build_matches(season, division, ordered_teams)

            plans.append(
                LeaguePlan(
                    code=league_code,
                    division_id=division["id"],
                    division_name=division.get("name", league_code),
                    season_id=season["id"],
                    season_name=season.get("name", "Unknown"),
                    matches=matches,
                    available_teams=len(ordered_teams),
                )
            )

            if sample_only:
                continue

        return plans

    def _load_team_order(self) -> Dict[str, List[Dict[str, str]]]:
        if not self.teams_file.exists():
            raise FileNotFoundError(f"teams file not found: {self.teams_file}")
        with self.teams_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        league_map: Dict[str, List[Dict[str, str]]] = {}
        for bucket, teams in payload.items():
            league_code = bucket.split("_")[0].upper()
            league_map.setdefault(league_code, [])
            for name, meta in teams.items():
                league_map[league_code].append({"name": name, "code": meta["code"]})
        return league_map

    def _fetch_divisions(self, leagues: Sequence[str]) -> Dict[str, Dict]:
        if not leagues:
            return {}
        client = self._client()
        response = (
            client
            .table("divisions")
            .select("id, name, league_type")
            .in_("league_type", leagues)
            .execute()
        )
        return {row["league_type"].upper(): row for row in (response.data or [])}

    def _fetch_teams(self, client, division_id: str) -> Dict[str, Dict]:
        response = (
            client
            .table("teams")
            .select("id, code, name")
            .eq("division_id", division_id)
            .execute()
        )
        return {row["code"].lower(): row for row in (response.data or [])}

    def _ensure_active_season(self, client, division: Dict) -> Dict:
        response = (
            client
            .table("seasons")
            .select("id, name, active")
            .eq("division_id", division["id"])
            .eq("active", True)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]

        season_name = f"S{datetime.now(timezone.utc).year % 100:02d}-TEST"
        insert_response = (
            client
            .table("seasons")
            .insert({
                "division_id": division["id"],
                "name": season_name,
                "active": True,
            })
            .execute()
        )
        if not insert_response.data:
            raise RuntimeError("Failed to create season for division %s" % division["league_type"])
        return insert_response.data[0]

    def _build_matches(self, season: Dict, division: Dict, teams: List[Dict]) -> List[Dict]:
        selected = teams[: min(4, len(teams))]
        if len(selected) < 2:
            return []

        pairings = []
        for idx in range(len(selected) - 1):
            pairings.append((selected[idx], selected[idx + 1]))
        if len(selected) > 2:
            pairings.append((selected[-1], selected[0]))

        rows: List[Dict] = []
        for idx, (home, away) in enumerate(pairings):
            team1_score = 70 + idx * 5
            team2_score = team1_score - (2 + idx % 3)
            played_at = (datetime.now(timezone.utc) - timedelta(days=idx + 1)).isoformat()
            rows.append({
                "season_id": season["id"],
                "division_id": division["id"],
                "team1_id": home["id"],
                "team2_id": away["id"],
                "team1_score": team1_score,
                "team2_score": team2_score,
                "status": "final",
                "played_at": played_at,
                "metadata": {
                    "seed_tag": SEED_TAG,
                    "seed_index": idx,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
            })
        return rows

    def _delete_seeded_matches(self, season_id: str) -> None:
        client = self._client()
        logger.info("Deleting previous seeded matches for season %s", season_id)
        client.table("matches").delete().match({"season_id": season_id}).contains("metadata", {"seed_tag": SEED_TAG}).execute()

    def _client(self):
        if self._supabase is None:
            self._supabase = get_supabase()
        return self._supabase


def main():
    parser = argparse.ArgumentParser(description="Seed sample standings data")
    parser.add_argument("--apply", action="store_true", help="Execute writes. Without this flag the script only previews")
    parser.add_argument("--reset", action="store_true", help="Delete previous seeded matches before inserting")
    args = parser.parse_args()

    seeder = StandingsSeeder()
    if not args.apply:
        plans = seeder.preview()
        print("Preview of sample standings seeding:")
        for plan in plans:
            print(f" - {plan.code}: {plan.available_teams} teams available, would insert {len(plan.matches)} matches into {plan.season_name}")
        print("Use --apply to write the sample matches.")
        return

    plans = seeder.apply(reset=args.reset)
    for plan in plans:
        logger.info("Seeded %d matches for %s %s", len(plan.matches), plan.code, plan.season_name)


if __name__ == "__main__":
    main()
