"""The standings image: layout, form column, zone legend, caching and failure handling."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from utils import standings_image_generator as gen
from utils.render import canvas
from utils.render.theme import DEFAULT_ACCENT


@pytest.fixture(autouse=True)
def fresh_cache():
    gen.standings_cache.clear()
    yield
    gen.standings_cache.clear()


def team(i, **overrides):
    row = {"team_id": str(i), "name": f"Team {i}", "code": f"team-{i}", "wins": 10 - i, "losses": i,
           "point_diff": 20 - 5 * i, "competition_points": 30 - i, "zone": None, "rank": i}
    row.update(overrides)
    return row


def open_png(buffer):
    return Image.open(io.BytesIO(buffer.getvalue()))


def render(rows, **kwargs):
    return gen.generate_simple_standings_image(rows, kwargs.pop("season", "Season 3"), kwargs.pop("league", "Premier"), **kwargs)


def test_nothing_to_show_gives_no_image():
    assert render([]) is None


def test_the_image_is_a_png_sized_to_its_rows():
    one, sixteen = open_png(render([team(1)])), open_png(render([team(i) for i in range(1, 17)]))

    assert one.format == "PNG" and sixteen.format == "PNG"
    assert sixteen.width == one.width
    assert sixteen.height - one.height == 15 * gen.ROW_HEIGHT


def test_the_form_column_widens_the_image_only_when_form_is_given():
    plain = open_png(render([team(1)]))
    with_form = open_png(render([team(1)], recent_results={"1": list("WLWWN")}))

    assert with_form.width - plain.width == gen.COL_FORM


def test_the_zone_legend_appears_only_when_a_zone_is_shown():
    no_zone = open_png(render([team(1), team(2)]))
    zoned = open_png(render([team(1, zone="promoted"), team(2, zone="relegated")]))

    assert zoned.height - no_zone.height == gen.LEGEND_HEIGHT


def test_each_zone_gets_a_coloured_stripe_on_its_row():
    image = open_png(render([team(1, zone="promoted"), team(2)])).convert("RGB")
    top = canvas.PADDING + canvas.ACCENT_HEIGHT + gen.HEADER_HEIGHT + gen.COLUMN_HEIGHT
    stripe_x = canvas.PADDING + gen.INNER_PAD + 1

    assert image.getpixel((stripe_x, top + 5)) == (46, 204, 113)  # first row: promoted
    assert image.getpixel((stripe_x, top + gen.ROW_HEIGHT + 5)) != (46, 204, 113)  # second row: no zone


def test_the_accent_strip_uses_the_given_colour_or_the_default():
    given = open_png(render([team(1)], accent_color="#FF0000")).convert("RGB")
    default = open_png(render([team(1)])).convert("RGB")

    assert given.getpixel((given.width // 2, canvas.PADDING + 2)) == (255, 0, 0)
    assert default.getpixel((default.width // 2, canvas.PADDING + 2)) == DEFAULT_ACCENT


def test_odd_data_does_not_break_the_image():
    rows = [
        team(1, name="An extremely long team name that must be cut to fit its column"),
        {"team_id": "2"},  # no name, code, record or zone at all
        team(3, point_diff=None, competition_points=None, zone="a zone nobody defined"),
    ]

    image = render(rows, recent_results={"1": ["W", "?", "L"], "2": [], "3": list("WWWWWWWW")})

    assert image is not None and open_png(image).format == "PNG"


def test_the_same_standings_are_rendered_once():
    rows = [team(1), team(2)]
    with patch.object(gen, "_render", wraps=gen._render) as render_spy:
        first = render(rows)
        second = render(rows)

    assert render_spy.call_count == 1
    assert first.getvalue() == second.getvalue()


def test_changed_standings_are_rendered_again():
    with patch.object(gen, "_render", wraps=gen._render) as render_spy:
        render([team(1)])
        render([team(1, wins=99)])

    assert render_spy.call_count == 2


def test_a_failure_gives_no_image_so_the_command_can_fall_back_to_text():
    with patch.object(gen, "_render", side_effect=RuntimeError("boom")):
        assert render([team(1)]) is None
