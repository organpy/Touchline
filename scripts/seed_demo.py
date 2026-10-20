"""Create demo divisions, teams and a first season on an EMPTY development database.

Usage (inside the virtual environment, with SUPABASE_URL/SUPABASE_KEY pointing at a dev project):

    python scripts/seed_demo.py                    # preview
    python scripts/seed_demo.py --apply            # 3 divisions x 6 teams, season in setup
    python scripts/seed_demo.py --apply --divisions 4 --teams 8

Never run this against the live league: it refuses when divisions or seasons already exist.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.league_repository import LeagueRepository  # noqa: E402
from db.supabase_client import get_supabase  # noqa: E402

logger = logging.getLogger("seed_demo")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

DIVISION_NAMES = ["Premier", "Championship", "League One", "League Two", "National", "Regional"]
DIVISION_COLORS = ["#F7AD44", "#5B8DEF", "#4CAF7A", "#C565E8", "#E8655B", "#8D99AE"]
TEAM_WORDS = [
    "Falcons", "Titans", "Hornets", "Sharks", "Wolves", "Comets", "Rangers", "Pilots",
    "Vipers", "Owls", "Bears", "Rockets", "Knights", "Foxes", "Lynx", "Storm",
]
PLACES = [
    "North", "South", "East", "West", "Harbor", "Valley", "Summit", "River",
    "Lake", "Forest", "Desert", "Coast", "Canyon", "Prairie", "Island", "Metro",
]
EMOJIS = ["🦅", "🗿", "🐝", "🦈", "🐺", "☄️", "🤠", "✈️", "🐍", "🦉", "🐻", "🚀", "⚔️", "🦊", "🐱", "⛈️"]


def demo_plan(divisions: int, teams_per_division: int):
    plan = []
    counter = 0
    for tier in range(1, divisions + 1):
        teams = []
        for _ in range(teams_per_division):
            place = PLACES[counter % len(PLACES)]
            word = TEAM_WORDS[(counter * 5 + tier) % len(TEAM_WORDS)]
            teams.append({"name": f"{place} {word}", "emoji": EMOJIS[counter % len(EMOJIS)]})
            counter += 1
        plan.append({
            "code": f"D{tier}",
            "name": DIVISION_NAMES[(tier - 1) % len(DIVISION_NAMES)],
            "color": DIVISION_COLORS[(tier - 1) % len(DIVISION_COLORS)],
            "teams": teams,
        })
    return plan


async def apply(plan) -> None:
    repo = LeagueRepository(get_supabase())
    if await repo.list_divisions(include_archived=True) or await repo.list_seasons():
        raise SystemExit("Refusing to seed: this database already has divisions or seasons.")
    season = await repo.create_first_season("Season 1")
    for division_spec in plan:
        division = await repo.create_division(
            code=division_spec["code"], name=division_spec["name"], color=division_spec["color"],
        )
        for team_spec in division_spec["teams"]:
            team = await repo.create_team(code="", name=team_spec["name"], emoji=team_spec["emoji"])
            await repo.assign_team(season["id"], team["id"], division["id"])
        logger.info("Created %s with %d teams", division["code"], len(division_spec["teams"]))
    logger.info("Season 1 is in setup. Configure rules and start it from /dashboard.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write to the database")
    parser.add_argument("--divisions", type=int, default=3)
    parser.add_argument("--teams", type=int, default=6, help="teams per division")
    args = parser.parse_args()

    plan = demo_plan(max(1, args.divisions), max(2, args.teams))
    for division in plan:
        names = ", ".join(t["name"] for t in division["teams"])
        print(f"{division['code']} {division['name']}: {names}")
    if not args.apply:
        print("\nPreview only. Re-run with --apply to write.")
        return
    load_dotenv()
    asyncio.run(apply(plan))


if __name__ == "__main__":
    main()
