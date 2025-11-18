"""Utility script to seed Supabase divisions and teams from teams.json.

Run inside the project virtual environment after configuring SUPABASE_URL and
SUPABASE_KEY in your .env file:

    python scripts/seed_teams.py --apply

Add --prune-missing to remove legacy teams that are no longer present in
teams.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.supabase_client import get_supabase

TEAMS_FILE = ROOT_DIR / "teams.json"

LEAGUE_METADATA = {
    "NBL": {"name": "NBL Prime"},
    "MBL": {"name": "MBL Prime"},
    "NCBA": {"name": "NCBA Prime"},
}

logger = logging.getLogger("seed_teams")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


@dataclass
class TeamDefinition:
    league_code: str
    conference: Optional[str]
    name: str
    code: str
    emoji: str

    @property
    def metadata(self) -> Dict[str, str]:
        meta = {"source": "teams.json"}
        if self.conference:
            meta["conference"] = self.conference
        return meta


class TeamSeeder:
    def __init__(self, teams_file: Path = TEAMS_FILE):
        self.teams_file = teams_file
        load_dotenv()
        self._supabase = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, prune_missing: bool = False) -> None:
        teams, leagues = self._load_team_definitions()
        logger.info("Loaded %s teams across %s leagues", len(teams), ", ".join(sorted(leagues)))

        division_map = self._ensure_divisions(leagues)
        logger.info("Divisions ready: %s", ", ".join(f"{code}→{meta['id']}" for code, meta in division_map.items()))

        updated_codes = self._upsert_teams(teams, division_map)
        logger.info("Upserted %s team rows", len(updated_codes))

        if prune_missing:
            removed = self._prune_missing_teams(updated_codes, list(division_map.values()))
            logger.info("Pruned %s legacy teams", removed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_team_definitions(self) -> Tuple[List[TeamDefinition], List[str]]:
        if not self.teams_file.exists():
            raise FileNotFoundError(f"teams file not found: {self.teams_file}")

        with self.teams_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        definitions: List[TeamDefinition] = []
        leagues: List[str] = []

        for bucket, teams in payload.items():
            league_prefix, _, conference_raw = bucket.partition("_")
            league_code = league_prefix.upper()
            conference = conference_raw.replace("_", " ").replace("-", " ").title() if conference_raw else None
            if league_code not in leagues:
                leagues.append(league_code)

            for name, meta in (teams or {}).items():
                definitions.append(
                    TeamDefinition(
                        league_code=league_code,
                        conference=conference,
                        name=name,
                        code=meta["code"],
                        emoji=meta["emoji"],
                    )
                )

        return definitions, leagues

    def _ensure_divisions(self, leagues: Sequence[str]) -> Dict[str, Dict[str, str]]:
        if not leagues:
            return {}

        client = self._client()

        response = (
            client
            .table("divisions")
            .select("id, league_type, name")
            .in_("league_type", leagues)
            .execute()
        )
        existing = {row["league_type"].upper(): row for row in (response.data or [])}

        missing = [code for code in leagues if code not in existing]
        if missing:
            upsert_rows = []
            for code in missing:
                meta = LEAGUE_METADATA.get(code, {})
                upsert_rows.append({
                    "league_type": code,
                    "name": meta.get("name", code),
                    "metadata": meta.get("metadata", {}),
                })
            client.table("divisions").upsert(upsert_rows, on_conflict="league_type").execute()
            # Refresh map after insertion
            response = (
                client
                .table("divisions")
                .select("id, league_type, name")
                .in_("league_type", leagues)
                .execute()
            )
            existing = {row["league_type"].upper(): row for row in (response.data or [])}

        missing_after = [code for code in leagues if code not in existing]
        if missing_after:
            raise RuntimeError(f"Unable to ensure divisions for: {', '.join(missing_after)}")

        return existing

    def _upsert_teams(self, teams: List[TeamDefinition], divisions: Dict[str, Dict[str, str]]) -> List[str]:
        if not teams:
            return []

        payload = []
        for team in teams:
            division = divisions.get(team.league_code)
            if not division:
                raise RuntimeError(f"No division found for league {team.league_code}")
            payload.append({
                "division_id": division["id"],
                "code": team.code,
                "name": team.name,
                "emoji": team.emoji,
                "metadata": team.metadata,
            })

        response = (
            self._client()
            .table("teams")
            .upsert(payload, on_conflict="code")
            .execute()
        )

        # When PostgREST returns empty data (depending on settings), fall back to provided codes
        if response.data:
            codes = [row.get("code", "").lower() for row in response.data]
        else:
            codes = [team.code.lower() for team in teams]
        return codes

    def _prune_missing_teams(self, valid_codes: Sequence[str], division_rows: Sequence[Dict[str, str]]) -> int:
        if not division_rows:
            return 0

        division_ids = [row["id"] for row in division_rows if row.get("id")]
        if not division_ids:
            return 0

        client = self._client()

        response = (
            client
            .table("teams")
            .select("id, code")
            .in_("division_id", division_ids)
            .execute()
        )
        existing = response.data or []
        valid_code_set = {code.lower() for code in valid_codes}

        stale_ids = [row["id"] for row in existing if row.get("code", "").lower() not in valid_code_set]
        removed = 0
        for chunk in _chunk(stale_ids, 50):
            if not chunk:
                continue
            client.table("teams").delete().in_("id", chunk).execute()
            removed += len(chunk)
        return removed

    # Lazily access Supabase so preview mode can run without credentials
    def _client(self):
        if self._supabase is None:
            self._supabase = get_supabase()
        return self._supabase


def _chunk(values: Sequence[str], size: int) -> List[List[str]]:
    return [list(values[i : i + size]) for i in range(0, len(values), size)]


def main():
    parser = argparse.ArgumentParser(description="Seed Supabase teams from teams.json")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required flag to execute writes. Use without flags to preview only.",
    )
    parser.add_argument(
        "--prune-missing",
        action="store_true",
        help="Remove teams in Supabase that are no longer listed in teams.json",
    )
    args = parser.parse_args()

    seeder = TeamSeeder()
    if not args.apply:
        teams, leagues = seeder._load_team_definitions()
        print("Preview: would upsert the following teams:")
        for team in teams:
            print(f" - {team.league_code} :: {team.name} ({team.code}) {team.emoji}")
        print("Use --apply to perform the upsert.")
        return

    seeder.run(prune_missing=args.prune_missing)


if __name__ == "__main__":
    main()
