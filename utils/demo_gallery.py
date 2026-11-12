"""Every generated image rendered from made-up data: used by ``scripts/preview_images.py`` and ``/demo gallery``."""

from __future__ import annotations

from utils import demo_data as demo
from utils.bracket_image_generator import generate_bracket_image
from utils.match_table_image_generator import generate_match_table_image
from utils.player_card_image_generator import generate_player_card_image
from utils.standings_image_generator import generate_simple_standings_image


def bracket_previews():
    """(filename, title, PNG buffer) for brackets in several states."""
    teams = demo.team_map()
    out = []

    def add(name, caption, bracket, title, subtitle, accent, **kwargs):
        out.append((name, caption, generate_bracket_image(bracket, teams, title, subtitle, accent, **kwargs)))

    fresh = demo.demo_bracket("championship", 8, best_of=3)
    add("bracket-8-not-started", "Championship, 8 teams, not started", fresh,
        "Championship", "D1 · Premier · Season 3", "#F7AD44")

    sim = demo.BracketSim(demo.demo_bracket("championship", 8, best_of=3), seed=4)
    sim.play_round()
    sim.play_game()
    add("bracket-8-in-progress", "Championship, 8 teams, quarterfinals done", sim.bracket,
        "Championship", "D1 · Premier · Season 3", "#F7AD44")

    sim = demo.BracketSim(demo.demo_bracket("championship", 6), seed=2)
    sim.play_all()
    add("bracket-6-byes-complete", "Championship, 6 teams with byes, finished", sim.bracket,
        "Championship", "D1 · Premier · Season 3", "#F7AD44")

    sim = demo.BracketSim(demo.demo_bracket("championship", 16), seed=9)
    sim.play_round()
    add("bracket-16", "Championship, 16 teams", sim.bracket, "Championship", "D1 · Premier · Season 3", "#5865F2")

    sim = demo.BracketSim(demo.demo_bracket("promotion", 8, spots=2), seed=1)
    sim.play_round()
    add("bracket-promotion-2-spots", "Promotion playoff, 8 teams for 2 spots", sim.bracket,
        "Promotion playoff", "D2 · Championship · Season 3", "#5B8DEF")

    sim = demo.BracketSim(demo.demo_bracket("relegation", 4), seed=3)
    sim.play_all()
    add("bracket-relegation", "Relegation playoff, losers advance, finished", sim.bracket,
        "Relegation playoff", "D1 · Premier · Season 3", "#E8655B")

    sim = demo.BracketSim(demo.demo_bracket("promo_rel", 6, spots=3, best_of=3), seed=5)
    sim.play_series()
    first = sim.ready()[0]
    sim.forfeit(first["id"], first["team2_id"])
    add("bracket-promo-rel", "Promotion/relegation series, one decided by forfeit", sim.bracket,
        "Promotion/relegation", "D1 · Premier v D2 · Championship", "#4CAF7A", result_label="Plays in D1")
    return out


def build():
    """(filename, title, PNG buffer) for each preview."""
    images = []

    rows = demo.standings_rows(8)
    images.append(("standings-full", "Standings with zones and form",
                   generate_simple_standings_image(rows, "Season 3", "D1 · Premier", "D1", demo.form_for(rows), "#5865F2")))
    images.append(("standings-no-form", "Standings without form",
                   generate_simple_standings_image(demo.standings_rows(4), "Season 3", "D2 · Championship", "D2", None, "#23A55A")))
    images.append(("standings-no-zones", "Standings, no zones, other accent",
                   generate_simple_standings_image(demo.standings_rows(5, with_zones=False), "Season 3", "D3", "D3",
                                                   demo.form_for(demo.standings_rows(5)), "#F0B232")))
    images.append(("standings-one-team", "Standings with one team",
                   generate_simple_standings_image(demo.standings_rows(1), "Season 1", "Only Division", None, None, None)))
    big = demo.standings_rows(16)
    images.append(("standings-sixteen", "Standings with sixteen teams",
                   generate_simple_standings_image(big, "Season 3", "Big League", None, demo.form_for(big), "#EB459E")))

    images.extend(bracket_previews())

    for count, games, name in ((4, 2, "match-table-2-games"), (3, 6, "match-table-6-games"), (6, 3, "match-table-3-games"), (8, 4, "match-table-4-games")):
        teams = demo.match_table_teams(count)
        images.append((name, f"Match table, {count} teams, {games} games per pairing",
                       generate_match_table_image(demo.played_games([t["id"] for t in teams], games), teams, "Season 3", "D1 · Premier", "D1", games)))

    career = demo.career()
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
