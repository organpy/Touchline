"""The match table image: slot layout, scores from the row team's side, caching and failure handling."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from utils import match_table_image_generator as gen
from utils.render import canvas
from utils.render.theme import CARD, LOSS, PENDING, WIN


@pytest.fixture(autouse=True)
def fresh_cache():
    gen.match_table_cache.clear()
    yield
    gen.match_table_cache.clear()


def teams(n=2):
    return [{"id": f"t{i}", "code": f"team-{i}", "name": f"Team {i}"} for i in range(1, n + 1)]


def game(home, away, home_score, away_score, status="final"):
    return {"team1_id": home, "team2_id": away, "team1_score": home_score, "team2_score": away_score, "status": status}


def render(matches, team_list=None, games=2, **kwargs):
    return gen.generate_match_table_image(matches, team_list or teams(), "Season 3", "Premier", games_per_matchup=games, **kwargs)


def open_png(buffer):
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def cell_origin(row, col, team_count, games=2):
    """Top-left pixel of the cell for (row team, column team), mirroring the generator's layout."""
    config = gen.matchup_layout(games)
    content_width = max(gen.LOGO_BAND + team_count * gen.CELL_WIDTH, gen.MIN_CONTENT_WIDTH)
    grid_width = gen.LOGO_BAND + team_count * gen.CELL_WIDTH
    top = canvas.PADDING + canvas.ACCENT_HEIGHT + gen.HEADER_HEIGHT
    left = canvas.PADDING + gen.INNER_PAD + (content_width - grid_width) // 2
    return left + gen.LOGO_BAND + col * gen.CELL_WIDTH, top + gen.LOGO_BAND + row * config["cell_height"]


# ----------------------------------------------------------------------------------------- layout


@pytest.mark.parametrize("games, slots, layout", [(1, 1, "vertical"), (2, 2, "vertical"), (4, 4, "vertical"),
                                                  (5, 5, "grid"), (6, 6, "grid"), (10, 10, "grid"), (50, 10, "grid"), (0, 2, "vertical")])
def test_layout_for_any_number_of_games(games, slots, layout):
    config = gen.matchup_layout(games)

    assert config["subcells"] == slots and config["layout"] == layout


@pytest.mark.parametrize("games", [1, 2, 3, 4, 5, 6, 7, 10])
def test_game_slots_fill_the_cell_without_overlapping(games):
    config = gen.matchup_layout(games)
    rects = gen._slot_rects(100, 200, config)

    assert len(rects) == config["subcells"]
    for x0, y0, x1, y1 in rects:
        assert 100 <= x0 < x1 <= 100 + gen.CELL_WIDTH and 200 <= y0 < y1 <= 200 + config["cell_height"]
    covered = {(x, y) for x0, y0, x1, y1 in rects for x in range(x0, x1) for y in range(y0, y1)}
    assert len(covered) == sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)  # no pixel claimed twice


# ----------------------------------------------------------------------------------------- scores


def test_a_home_game_reads_from_the_home_teams_side():
    assert gen._result_for(True, game("a", "b", 66, 60)) == ("66–60", True)
    assert gen._result_for(True, game("a", "b", 50, 60)) == ("50–60", False)


def test_an_away_game_reads_from_the_away_teams_side():
    assert gen._result_for(False, game("a", "b", 66, 60)) == ("60–66", False)
    assert gen._result_for(False, game("a", "b", 50, 60)) == ("60–50", True)


def test_a_game_that_is_not_final_or_missing_has_no_result():
    assert gen._result_for(True, None) is None
    assert gen._result_for(True, game("a", "b", 1, 2, status="void")) is None
    assert gen._result_for(True, game("a", "b", None, None, status="scheduled")) is None


# ----------------------------------------------------------------------------------------- image


def test_no_teams_means_no_image():
    assert gen.generate_match_table_image([], [], "S1", "L") is None


def test_the_image_grows_with_the_number_of_teams():
    two, five = open_png(render([], teams(2))), open_png(render([], teams(5)))

    assert five.height - two.height == 3 * gen.matchup_layout(2)["cell_height"]
    assert five.width > two.width


def colors_in(image, rect):
    x0, y0, x1, y1 = rect
    return {image.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1)}


def score_color(image, rect):
    """WIN, LOSS or None: the colour a slot's score is written in (slots are neutral; only the text is coloured)."""
    found = colors_in(image, rect)
    assert not (WIN in found and LOSS in found)
    return WIN if WIN in found else LOSS if LOSS in found else None


def is_empty_slot(image, rect):
    """An unplayed game: an outlined slot with nothing in it."""
    x0, y0, x1, y1 = rect
    return image.getpixel(((x0 + x1) // 2, y0 + 2)) == PENDING and score_color(image, rect) is None


def test_wins_are_green_losses_red_and_unplayed_games_empty_from_the_row_teams_side():
    matches = [game("t1", "t2", 66, 60), game("t2", "t1", 70, 64)]  # t1 won at home, lost away
    image = open_png(render(matches))
    x, y = cell_origin(0, 1, 2)  # row t1, column t2
    home_slot, away_slot = gen._slot_rects(x, y, gen.matchup_layout(2))

    assert score_color(image, home_slot) == WIN
    assert score_color(image, away_slot) == LOSS

    x, y = cell_origin(1, 0, 2)  # the mirror cell: row t2, column t1 shows the same games from t2's side
    home_slot, away_slot = gen._slot_rects(x, y, gen.matchup_layout(2))
    assert score_color(image, home_slot) == WIN  # t2 won its home game 70-64
    assert score_color(image, away_slot) == LOSS  # t2 lost its away game 60-66


def test_games_not_played_yet_are_empty_and_a_team_against_itself_has_no_slots():
    image = open_png(render([]))
    x, y = cell_origin(0, 1, 2)
    slot = gen._slot_rects(x, y, gen.matchup_layout(2))[0]
    diagonal = cell_origin(0, 0, 2)

    assert is_empty_slot(image, slot)
    assert image.getpixel((diagonal[0] + 5, diagonal[1] + 5)) == CARD
    assert PENDING not in colors_in(image, (diagonal[0], diagonal[1], diagonal[0] + gen.CELL_WIDTH, diagonal[1] + 70))


def test_a_grid_layout_also_places_scores_by_slot():
    matches = [game("t1", "t2", 66, 60), game("t1", "t2", 10, 20), game("t2", "t1", 70, 64)]
    image = open_png(render(matches, games=6))
    x, y = cell_origin(0, 1, 2, games=6)
    slots = gen._slot_rects(x, y, gen.matchup_layout(6))

    assert score_color(image, slots[0]) == WIN    # 1st home game
    assert score_color(image, slots[1]) == LOSS   # 1st away game (t2 hosted, t1 lost 64-70)
    assert score_color(image, slots[2]) == LOSS   # 2nd home game 10-20
    assert is_empty_slot(image, slots[3])         # 2nd away game not played


def test_more_games_than_slots_are_ignored():
    matches = [game("t1", "t2", 1, 0), game("t1", "t2", 1, 0), game("t1", "t2", 1, 0)]

    assert render(matches) is not None  # a second home game has no slot with 2 games per pairing; no crash


def test_long_names_and_missing_fields_do_not_break_it():
    odd_teams = [{"id": "a"}, {"id": "b", "name": "A very long team name " * 5}]

    assert render([{"team1_id": "a", "team2_id": "b"}, {}], odd_teams, league_code=None) is not None


# ----------------------------------------------------------------------------------------- cache


def test_the_same_table_is_rendered_once_and_a_new_result_renders_again():
    matches = [game("t1", "t2", 66, 60)]
    with patch.object(gen, "_render", wraps=gen._render) as spy:
        first, second = render(matches), render(matches)
        render(matches + [game("t2", "t1", 70, 64)])

    assert spy.call_count == 2
    assert first.getvalue() == second.getvalue()


def test_a_failure_gives_no_image():
    with patch.object(gen, "_render", side_effect=RuntimeError("boom")):
        assert render([]) is None
