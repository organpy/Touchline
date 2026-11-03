"""Tests for the shared image toolkit: theme, fonts, logos, card, text fitting and the render cache."""

import io
from unittest.mock import patch

import pytest
from PIL import Image

from utils.league_cache import LeagueCache
from utils.render import canvas, fonts, logos, theme


# ------------------------------------------------------------------------------------------ theme


@pytest.mark.parametrize("value, expected", [
    ("#1A2B3C", (26, 43, 60)), ("1a2b3c", (26, 43, 60)), ("#abc", (170, 187, 204)),
    (None, None), ("", None), ("#12345", None), ("#GGGGGG", None), (123, None),
])
def test_parse_hex(value, expected):
    assert theme.parse_hex(value) == expected


def test_accent_prefers_the_given_colour_then_the_division_then_the_default():
    LeagueCache.load_snapshot(seasons=[], divisions=[{"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "color": "#FF0000"}])
    try:
        assert theme.accent_color("#00FF00", "D1") == (0, 255, 0)
        assert theme.accent_color(None, "D1") == (255, 0, 0)
        assert theme.accent_color(None, "ZZ") == theme.DEFAULT_ACCENT
        assert theme.accent_color(None, None) == theme.DEFAULT_ACCENT
        assert theme.accent_color("not a colour", None) == theme.DEFAULT_ACCENT
    finally:
        LeagueCache.invalidate()


# ------------------------------------------------------------------------------------------ fonts


def test_fonts_are_loaded_once_per_size_and_weight():
    assert fonts.font(14) is fonts.font(14)
    assert fonts.font(14) is not fonts.font(14, bold=True)
    assert fonts.font(14).getlength("Hello") > 0


def test_a_missing_font_file_falls_back_to_the_default_font(tmp_path):
    fonts.font.cache_clear()
    try:
        with patch.object(fonts, "FONTS_DIR", tmp_path):
            assert fonts.font(12).getlength("Hello") > 0
    finally:
        fonts.font.cache_clear()


# ------------------------------------------------------------------------------------------ logos


@pytest.fixture
def logo_dirs(tmp_path):
    bundled, uploaded = tmp_path / "images", tmp_path / "logo_cache"
    bundled.mkdir()
    uploaded.mkdir()
    logos.clear()
    with patch.object(logos, "IMAGES_DIR", bundled), patch.object(logos, "LOGO_CACHE_DIR", uploaded):
        yield bundled, uploaded
    logos.clear()


def _save(path, colour, size=(40, 20)):
    Image.new("RGBA", size, colour).save(path)


def test_logo_is_found_by_code_or_name_and_resized_to_a_square(logo_dirs):
    bundled, _ = logo_dirs
    _save(bundled / "frost-falcons.png", (255, 0, 0, 255))

    picture = logos.logo(30, "ZZZ", "Frost Falcons")

    assert picture.size == (30, 30)
    assert picture.getpixel((15, 15))[:3] == (255, 0, 0)
    assert logos.logo(30, "nobody", None) is None


def test_an_uploaded_logo_wins_over_a_bundled_one(logo_dirs):
    bundled, uploaded = logo_dirs
    _save(bundled / "titans.png", (255, 0, 0, 255))
    _save(uploaded / "titans.png", (0, 0, 255, 255))

    assert logos.logo(20, "titans").getpixel((10, 10))[:3] == (0, 0, 255)


def test_clear_makes_new_uploads_visible(logo_dirs):
    _, uploaded = logo_dirs
    assert logos.logo(20, "owls") is None

    _save(uploaded / "owls.png", (0, 255, 0, 255))
    assert logos.logo(20, "owls") is None  # the file list is remembered...
    logos.clear()
    assert logos.logo(20, "owls") is not None  # ...until it is cleared


def test_a_broken_logo_file_is_ignored(logo_dirs):
    bundled, _ = logo_dirs
    (bundled / "bad.png").write_bytes(b"not an image")

    assert logos.logo(20, "bad") is None


# ------------------------------------------------------------------------------------------ card


def test_card_has_an_accent_strip_along_its_top_edge_only():
    image, _ = canvas.new_image(300, 200, (255, 0, 0))

    assert image.size == (300, 200)
    assert image.getpixel((5, 5)) == theme.BG  # outside the card
    assert image.getpixel((150, canvas.PADDING + 2)) == (255, 0, 0)  # accent strip
    assert image.getpixel((150, canvas.PADDING + canvas.ACCENT_HEIGHT + 10)) == theme.CARD
    assert image.getpixel((150, 200 - canvas.PADDING - 10)) == theme.CARD


def test_card_corners_are_rounded():
    image, _ = canvas.new_image(300, 200, (255, 0, 0))

    assert image.getpixel((canvas.PADDING, canvas.PADDING)) == theme.BG


# ------------------------------------------------------------------------------------------ text


def test_text_that_fits_is_left_alone_and_long_text_gets_an_ellipsis():
    fnt = fonts.font(14)
    assert canvas.fit_text("  Short  ", fnt, 500) == "Short"

    fitted = canvas.fit_text("An extremely long team name that cannot possibly fit", fnt, 120)
    assert fitted.endswith("…")
    assert fnt.getlength(fitted) <= 120


def test_when_not_even_one_letter_fits_only_an_ellipsis_is_left():
    assert canvas.fit_text("Something", fonts.font(14), 3) == "…"


def test_empty_text_stays_empty():
    fnt = fonts.font(14)
    assert canvas.fit_text("", fnt, 100) == ""
    assert canvas.fit_text(None, fnt, 100) == ""


# ------------------------------------------------------------------------------------------ logo drawing


def test_a_missing_logo_becomes_a_circle_with_the_first_letter(logo_dirs):
    image, draw = canvas.new_image(200, 120, (255, 0, 0))

    canvas.draw_logo(image, draw, (100, 60), 40, ["nobody"], "Zebras")

    assert image.getpixel((100 + 17, 60)) == theme.DIVIDER  # inside the circle, away from the letter
    assert image.getpixel((100 + 30, 60)) == theme.CARD  # outside it


def test_an_existing_logo_is_pasted_centred(logo_dirs):
    bundled, _ = logo_dirs
    _save(bundled / "sharks.png", (0, 255, 0, 255), size=(40, 40))
    image, draw = canvas.new_image(200, 120, (255, 0, 0))

    canvas.draw_logo(image, draw, (100, 60), 40, ["sharks"], "Sharks")

    assert image.getpixel((100, 60)) == (0, 255, 0)
    assert image.getpixel((100 + 25, 60)) == theme.CARD


# ------------------------------------------------------------------------------------------ cache


def test_render_cache_returns_the_same_png_until_cleared():
    cache = canvas.RenderCache()
    key = cache.key({"b": 2, "a": [1, 2]})
    assert key == cache.key({"a": [1, 2], "b": 2})  # key order does not matter
    assert cache.get(key) is None

    first = cache.put(key, Image.new("RGB", (10, 10), (1, 2, 3)))
    again = cache.get(key)

    assert first.getvalue() == again.getvalue()
    assert first is not again
    assert Image.open(io.BytesIO(again.getvalue())).getpixel((0, 0)) == (1, 2, 3)

    cache.clear()
    assert cache.get(key) is None


def test_uploading_a_logo_clears_the_shared_logo_cache(logo_dirs):
    from utils import logo_store

    _, uploaded = logo_dirs
    assert logos.logo(20, "newteam") is None

    _save(uploaded / "newteam.png", (0, 255, 0, 255))
    logo_store.clear_image_indexes()

    assert logos.logo(20, "newteam") is not None
