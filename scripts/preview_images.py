"""Render every generated image with dummy data, so the look can be checked without Discord or a database.

    python scripts/preview_images.py            # writes preview_output/*.png and preview_output/index.html
    python scripts/preview_images.py --open     # also opens the gallery in your browser
    python scripts/preview_images.py --out /tmp/previews
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.match_table_image_generator import generate_match_table_image  # noqa: E402
from utils.player_card_image_generator import generate_player_card_image  # noqa: E402
from utils.standings_image_generator import generate_simple_standings_image  # noqa: E402

# Teams with bundled logos (images/), then ones without, so both looks are shown
TEAMS = [
    ("frost-falcons", "Frost Falcons"), ("titans", "Titans"), ("the-herd", "The Herd"), ("ghosts", "Ghosts"),
    ("icebergs", "Icebergs"), ("the-pack", "The Pack"), ("north-central-hawks", "North Central Hawks"),
    ("blue-ballers", "Blue Ballers"), ("no-logo-fc", "No Logo FC"), ("the-extraordinarily-long-named-herd", "The Extraordinarily Long Named Herd of Mighty Bison"),
]
ZONES = ["champion_playoff", "champion_playoff", "promoted", None, None, None, "relegation_playoff", "relegated"]


def standings_rows(count: int, with_zones: bool = True):
    rows = []
    for index in range(count):
        code, name = TEAMS[index % len(TEAMS)]
        wins = max(count - index - 1, 0)
        rows.append({
            "team_id": str(index + 1), "name": name, "code": code, "rank": index + 1, "wins": wins, "losses": index,
            "point_diff": (count // 2 - index) * 9, "competition_points": wins * 3 + 2,
            "zone": ZONES[index % len(ZONES)] if with_zones else None,
        })
    return rows


def form_for(rows):
    patterns = ["WWWLW", "WLWWW", "LWWLW", "LLLWN", "NNNNN"]
    return {row["team_id"]: list(patterns[i % len(patterns)]) for i, row in enumerate(rows)}


def played_games(team_ids, games_per_pair: int):
    """Home and away games between every pair, partly played, so every cell colour shows up."""
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
                matches.append({"team1_id": home, "team2_id": away, "team1_score": home_score, "team2_score": away_score, "status": "final"})
    return matches


def season(number, team, **stats):
    row = {"season_id": f"s{number}", "season_number": number, "season_name": f"S{number}", "team_name": team,
           "games_played": 10, "goals": 12, "assists": 7, "saves": 30, "blocks": 5, "points": 54}
    row.update(stats)
    return row


def build():
    """(filename, title, PNG buffer) for each preview."""
    images = []

    rows = standings_rows(8)
    images.append(("standings-full", "Standings with zones and form",
                   generate_simple_standings_image(rows, "Season 3", "D1 · Premier", "D1", form_for(rows), "#5865F2")))
    images.append(("standings-no-form", "Standings without form",
                   generate_simple_standings_image(standings_rows(4), "Season 3", "D2 · Championship", "D2", None, "#23A55A")))
    images.append(("standings-no-zones", "Standings, no zones, other accent",
                   generate_simple_standings_image(standings_rows(5, with_zones=False), "Season 3", "D3", "D3", form_for(standings_rows(5)), "#F0B232")))
    images.append(("standings-one-team", "Standings with one team",
                   generate_simple_standings_image(standings_rows(1), "Season 1", "Only Division", None, None, None)))
    big = standings_rows(16)
    images.append(("standings-sixteen", "Standings with sixteen teams",
                   generate_simple_standings_image(big, "Season 3", "Big League", None, form_for(big), "#EB459E")))

    for count, games, name in ((4, 2, "match-table-2-games"), (3, 6, "match-table-6-games"), (6, 3, "match-table-3-games"), (8, 4, "match-table-4-games")):
        teams = [{"id": str(i + 1), "code": TEAMS[i][0], "name": TEAMS[i][1]} for i in range(count)]
        images.append((name, f"Match table, {count} teams, {games} games per pairing",
                       generate_match_table_image(played_games([t["id"] for t in teams], games), teams, "Season 3", "D1 · Premier", "D1", games)))

    career = [season(1, "Titans", goals=14, points=52, games_played=8), season(2, "Frost Falcons", goals=21, points=80),
              season(3, "Frost Falcons")]
    images.append(("player-card-veteran", "Player card, three seasons",
                   generate_player_card_image("Ace", "Frost Falcons", "frost-falcons", "D1", "Season 3", career[2], career)))
    images.append(("player-card-no-games-this-season", "Player card, no games this season",
                   generate_player_card_image("Zed", "The Herd", "the-herd", "D2", "Season 4", None, career[:2])))
    images.append(("player-card-rookie", "Player card, no games yet",
                   generate_player_card_image("New Player", None, None, None, "Season 1", None, [])))
    images.append(("player-card-long-names", "Player card, very long names",
                   generate_player_card_image("A Player With An Impossibly Long Display Name Indeed", "The Extraordinarily Long Named Herd of Mighty Bison",
                                              "no-such-logo", "D1", "Season 3 (Winter Championship Edition)", career[2], career)))
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "preview_output", help="folder for the images (default: preview_output)")
    parser.add_argument("--open", action="store_true", help="open the gallery in the default browser")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    figures, failed = [], []
    for filename, title, buffer in build():
        if buffer is None:
            failed.append(filename)
            continue
        (args.out / f"{filename}.png").write_bytes(buffer.getvalue())
        figures.append(f'<figure><figcaption>{html.escape(title)}</figcaption><img src="{filename}.png" alt="{html.escape(title)}"></figure>')
        print(f"wrote {args.out / (filename + '.png')}")

    gallery = args.out / "index.html"
    gallery.write_text(
        "<!doctype html><meta charset=utf-8><title>Touchline image previews</title>"
        "<style>body{background:#1e1f22;color:#f2f3f5;font:14px sans-serif;margin:24px}"
        "figure{margin:0 0 32px}figcaption{margin-bottom:8px;color:#b5bac1}img{max-width:100%}</style>"
        + "".join(figures),
        encoding="utf-8",
    )
    print(f"\ngallery: {gallery}")
    if failed:
        print("FAILED to render:", ", ".join(failed))
        return 1
    if args.open:
        webbrowser.open(gallery.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
