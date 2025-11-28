"""Modern standings rendering with PNG logos, dark canvas, gradients, and caching."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from utils.caching import TTLCache

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT_DIR / "images"
FONTS_DIR = ROOT_DIR / "fonts"

BASE_THEME = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
    "header_overlay": None,
    "header_title_color": (245, 248, 255, 255),
    "header_title_shadow": None,
    "header_subtitle_color": (173, 187, 212, 255),
    "header_subtitle_shadow": None,
    "text_primary": (245, 248, 255, 255),
    "text_muted": (173, 187, 212, 255),
    "text_stats": (223, 233, 255, 255),
    "row_primary": (35, 46, 67, 255),
    "row_alt": (40, 52, 76, 255),
    "row_divider": (56, 68, 94, 255),
    "rank_accent": {
        1: (255, 197, 80, 255),
        2: (200, 200, 220, 255),
        3: (209, 148, 90, 255),
    },
}

LEAGUE_THEME_OVERRIDES = {
    "NBL": {
        "canvas_gradient": ((45, 30, 18), (95, 60, 28)),
        "card_bg": (52, 35, 20, 255),
        "card_border": (111, 74, 38, 255),
        "header_gradient": ((192, 142, 86), (126, 74, 33)),
        "header_overlay": (0, 0, 0, 120),
        "header_title_color": (255, 250, 240, 255),
        "header_title_shadow": (0, 0, 0, 160),
        "header_subtitle_color": (255, 227, 176, 255),
        "header_subtitle_shadow": (0, 0, 0, 140),
        "text_primary": (255, 250, 240, 255),
        "text_muted": (236, 210, 160, 255),
        "text_stats": (234, 199, 141, 255),
        "row_primary": (64, 44, 24, 255),
        "row_alt": (55, 36, 20, 255),
        "row_divider": (122, 86, 44, 255),
        "rank_accent": {
            1: (255, 234, 173, 255),
            2: (200, 175, 150, 255),
            3: (248, 203, 129, 255),
        },
    },
    "MBL": {
        "canvas_gradient": ((29, 6, 6), (70, 21, 2)),
        # Base MBL header on #FF9602 (orange) with a darker orange tail
        "header_gradient": ((255, 150, 2), (191, 105, 2)),
        "row_primary": (58, 34, 25, 255),
        "row_alt": (64, 38, 28, 255),
        "card_bg": (42, 24, 18, 255),
        "card_border": (84, 47, 34, 255),
    },
    "NCBA": {
        "canvas_gradient": ((5, 24, 24), (9, 58, 70)),
        "header_gradient": ((0, 173, 181), (0, 119, 182)),
        "row_primary": (16, 46, 60, 255),
        "row_alt": (18, 54, 70, 255),
        "card_bg": (14, 34, 44, 255),
        "card_border": (22, 64, 78, 255),
    },
}

STANDINGS_CACHE_TTL_SECONDS = 120


def _to_rgba(color: Tuple[int, ...]) -> Tuple[int, int, int, int]:
    if len(color) == 4:
        return color  # already RGBA
    if len(color) == 3:
        return (color[0], color[1], color[2], 255)
    raise ValueError("Color tuples must be length 3 or 4")


def _get_theme(league_code: Optional[str]) -> Dict[str, Any]:
    theme = {**BASE_THEME}
    theme["rank_accent"] = BASE_THEME["rank_accent"].copy()

    if league_code:
        overrides = LEAGUE_THEME_OVERRIDES.get(league_code.upper())
        if overrides:
            # Handle nested rank accents carefully to avoid shared references
            if "rank_accent" in overrides:
                theme["rank_accent"] = overrides["rank_accent"].copy()
            theme.update({k: v for k, v in overrides.items() if k != "rank_accent"})

    return theme

# Layout constants
CANVAS_PADDING = 48
CARD_CORNER_RADIUS = 36
HEADER_BLOCK_HEIGHT = 150
HEADER_TOP_PADDING = 32
HEADER_SIDE_PADDING = 32
ROW_HEIGHT = 68
HEADER_HEIGHT = 56
INNER_MARGIN_X = 70
TABLE_MARGIN_TOP = 24
TABLE_MARGIN_BOTTOM = 48
COL_RANK = 78
COL_LOGO = 70
COL_TEAM = 240
COL_W = 58
COL_L = 58
COL_PF = 70
COL_PA = 70
COL_DIFF = 80
TEAM_LOGO_SIZE = 48
LEAGUE_LOGO_SIZE = 96


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=1)
def _image_index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    if not IMAGES_DIR.exists():
        return index

    for file_path in IMAGES_DIR.iterdir():
        if file_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        index[_normalize_key(file_path.stem)] = file_path
    return index


standings_image_cache = TTLCache(ttl_seconds=STANDINGS_CACHE_TTL_SECONDS, max_size=32)


def clear_standings_image_cache() -> None:
    """Clear cached standings images (mainly for tests)."""
    standings_image_cache.clear()


def _make_cache_key(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str],
) -> str:
    payload = {
        "league_code": (league_code or "").upper(),
        "league_name": league_name,
        "season_name": season_name,
        "standings": standings_data,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _bytes_to_buffer(image_bytes: bytes) -> io.BytesIO:
    buffer = io.BytesIO(image_bytes)
    buffer.seek(0)
    return buffer


def _find_logo_path(*candidates: Optional[str]) -> Optional[Path]:
    index = _image_index()
    for candidate in candidates:
        if not candidate:
            continue
        key = _normalize_key(candidate)
        if key in index:
            return index[key]
    return None


def _load_logo(path: Path, size: int) -> Optional[Image.Image]:
    try:
        image = Image.open(path).convert("RGBA")
        return _prepare_logo_image(image, size)
    except Exception:
        logger.warning("Failed to load logo %s", path)
        return None


def _prepare_logo_image(image: Image.Image, size: int) -> Image.Image:
    image.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - image.width) // 2, (size - image.height) // 2)
    canvas.paste(image, offset, image)
    return canvas


def _draw_vertical_gradient(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], start_color, end_color):
    x0, y0, x1, y1 = bbox
    height = max(y1 - y0, 1)
    for i in range(height):
        ratio = i / height
        r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
        g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
        b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
        draw.line([(x0, y0 + i), (x1, y0 + i)], fill=(r, g, b))


def _load_fonts():
    try:
        title_font = ImageFont.truetype(FONTS_DIR / "Poppins-Bold.ttf", 28)
        subtitle_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 16)
        header_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 14)
        body_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 13)
    except Exception:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        header_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    return title_font, subtitle_font, header_font, body_font


def _draw_circle_avatar(
    base: Image.Image,
    center: Tuple[int, int],
    diameter: int,
    text: str,
    *,
    fill_color: Tuple[int, int, int, int],
    outline_color: Tuple[int, int, int, int],
    text_color: Tuple[int, int, int, int],
):
    draw = ImageDraw.Draw(base)
    radius = diameter // 2
    x0, y0 = center[0] - radius, center[1] - radius
    x1, y1 = center[0] + radius, center[1] + radius
    draw.ellipse((x0, y0, x1, y1), fill=fill_color, outline=outline_color, width=2)
    text_font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=text_font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((center[0] - w / 2, center[1] - h / 2), text, fill=text_color, font=text_font)


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height


def _truncate_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if not text:
        return ""

    clean_text = text.strip()
    if not clean_text:
        return ""

    width, _ = _measure_text(draw, clean_text, font)
    if width <= max_width:
        return clean_text

    ellipsis = "…"
    trimmed = clean_text
    while trimmed:
        trimmed = trimmed[:-1]
        candidate = f"{trimmed}{ellipsis}"
        cand_width, _ = _measure_text(draw, candidate, font)
        if cand_width <= max_width:
            return candidate

    return ellipsis


def generate_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
) -> Optional[io.BytesIO]:
    try:
        if not standings_data:
            return None

        cache_key = _make_cache_key(standings_data, season_name, league_name, league_code)
        cached_image = standings_image_cache.get(cache_key)
        if cached_image is not None:
            logger.debug("Standings image cache hit for key %s", cache_key[:8])
            return _bytes_to_buffer(cached_image)

        theme = _get_theme(league_code)

        team_column_width = COL_TEAM
        if standings_data:
            longest_name = max(len(team.get("name", "")) for team in standings_data)
            # Scale roughly by character count but keep sensible bounds
            team_column_width = min(max(COL_TEAM, longest_name * 12), 360)

        if (league_code or "").upper() == "NCBA":
            team_column_width = max(team_column_width, COL_TEAM + 40)

        table_width = (
            COL_RANK
            + COL_LOGO
            + team_column_width
            + COL_W
            + COL_L
            + COL_PF
            + COL_PA
            + COL_DIFF
        )
        table_height = HEADER_HEIGHT + ROW_HEIGHT * len(standings_data)
        card_width = table_width + (INNER_MARGIN_X * 2)
        card_height = (
            HEADER_TOP_PADDING
            + HEADER_BLOCK_HEIGHT
            + TABLE_MARGIN_TOP
            + table_height
            + TABLE_MARGIN_BOTTOM
        )
        canvas_width = card_width + CANVAS_PADDING * 2
        canvas_height = card_height + CANVAS_PADDING * 2

        canvas = Image.new("RGBA", (canvas_width, canvas_height), _to_rgba(theme["canvas_gradient"][0]))
        canvas_draw = ImageDraw.Draw(canvas)
        _draw_vertical_gradient(canvas_draw, (0, 0, canvas_width, canvas_height), *theme["canvas_gradient"])

        card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle(
            (0, 0, card_width, card_height),
            radius=CARD_CORNER_RADIUS,
            fill=theme["card_bg"],
            outline=theme["card_border"],
            width=2,
        )

        title_font, subtitle_font, header_font, body_font = _load_fonts()

        header_width = card_width - (HEADER_SIDE_PADDING * 2)
        header_x = HEADER_SIDE_PADDING
        header_y = HEADER_TOP_PADDING
        header_bbox = (0, 0, header_width, HEADER_BLOCK_HEIGHT)

        header_layer = Image.new("RGBA", (header_width, HEADER_BLOCK_HEIGHT), (0, 0, 0, 0))
        header_layer_draw = ImageDraw.Draw(header_layer)
        _draw_vertical_gradient(header_layer_draw, header_bbox, *theme["header_gradient"])

        header_overlay = theme.get("header_overlay")
        if header_overlay:
            header_layer_draw.rectangle(header_bbox, fill=header_overlay)

        header_mask = Image.new("L", (header_width, HEADER_BLOCK_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(header_mask)
        mask_draw.rounded_rectangle(
            (0, 0, header_width, HEADER_BLOCK_HEIGHT + CARD_CORNER_RADIUS),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        mask_draw.rectangle(
            (0, CARD_CORNER_RADIUS, header_width, HEADER_BLOCK_HEIGHT),
            fill=255,
        )

        card.paste(header_layer, (header_x, header_y), header_mask)

        league_logo = _find_logo_path(league_code, league_name)
        logo_y = header_y + (HEADER_BLOCK_HEIGHT - LEAGUE_LOGO_SIZE) // 2
        logo_x = header_x + 8
        if league_logo:
            logo_img = _load_logo(league_logo, LEAGUE_LOGO_SIZE)
        else:
            logo_img = None

        if logo_img:
            card.paste(logo_img, (logo_x, logo_y), logo_img)
        else:
            _draw_circle_avatar(
                card,
                (logo_x + LEAGUE_LOGO_SIZE // 2, logo_y + LEAGUE_LOGO_SIZE // 2),
                LEAGUE_LOGO_SIZE,
                (league_code or "L")[:2].upper(),
                fill_color=_to_rgba(theme["row_primary"]),
                outline_color=_to_rgba(theme["card_border"]),
                text_color=_to_rgba(theme["text_primary"]),
            )

        text_x = logo_x + LEAGUE_LOGO_SIZE + 32
        header_center_y = header_y + HEADER_BLOCK_HEIGHT // 2
        title_color = theme.get("header_title_color", theme["text_primary"])
        subtitle_color = theme.get("header_subtitle_color", theme["text_muted"])
        title_shadow = theme.get("header_title_shadow")
        subtitle_shadow = theme.get("header_subtitle_shadow")

        title_position = (text_x, header_center_y - 28)
        subtitle_position = (text_x, header_center_y + 8)

        if title_shadow:
            card_draw.text((title_position[0] + 2, title_position[1] + 2), league_name, fill=title_shadow, font=title_font)
        card_draw.text(title_position, league_name, fill=title_color, font=title_font)

        season_label = (season_name or "Season").upper()
        clubs_text = f"{len(standings_data)} CLUBS" if standings_data else "STANDINGS"
        subtitle_text = f"{season_label} • {clubs_text}"
        if subtitle_shadow:
            card_draw.text((subtitle_position[0] + 1, subtitle_position[1] + 1), subtitle_text, fill=subtitle_shadow, font=subtitle_font)
        card_draw.text(subtitle_position, subtitle_text, fill=subtitle_color, font=subtitle_font)

        table_x = INNER_MARGIN_X
        table_y = header_y + HEADER_BLOCK_HEIGHT + TABLE_MARGIN_TOP
        card_draw.rectangle((table_x, table_y, table_x + table_width, table_y + HEADER_HEIGHT), fill=theme["row_primary"])

        columns = [
            ("", COL_RANK),
            ("", COL_LOGO),
            ("Team", team_column_width),
            ("W", COL_W),
            ("L", COL_L),
            ("PF", COL_PF),
            ("PA", COL_PA),
            ("Diff", COL_DIFF),
        ]

        x = table_x
        for text, width in columns:
            if text:
                text_w, text_h = _measure_text(card_draw, text, header_font)
                text_x = x + width / 2 - text_w / 2
                if text.lower() == "team":
                    text_x = x + 8

                card_draw.text(
                    (text_x, table_y + HEADER_HEIGHT / 2 - text_h / 2),
                    text,
                    fill=theme["text_muted"],
                    font=header_font,
                )
            x += width

        y = table_y + HEADER_HEIGHT
        for rank, team in enumerate(standings_data, 1):
            row_top = y
            row_bottom = y + ROW_HEIGHT
            row_color = theme["row_primary"] if rank % 2 else theme["row_alt"]
            card_draw.rectangle((table_x, row_top, table_x + table_width, row_bottom), fill=row_color)
            card_draw.line((table_x, row_bottom, table_x + table_width, row_bottom), fill=theme["row_divider"], width=1)

            rank_accent = theme["rank_accent"].get(rank)
            if rank_accent:
                card_draw.rectangle((table_x, row_top, table_x + 6, row_bottom), fill=rank_accent)

            x = table_x
            rank_text = str(rank)
            text_w, text_h = _measure_text(card_draw, rank_text, body_font)
            card_draw.text(
                (x + COL_RANK / 2 - text_w / 2, row_top + ROW_HEIGHT / 2 - text_h / 2),
                rank_text,
                fill=theme["text_primary"],
                font=body_font,
            )
            x += COL_RANK

            team_logo_path = _find_logo_path(team.get("code"), team.get("name"))
            logo_center = (int(x + COL_LOGO / 2), int(row_top + ROW_HEIGHT / 2))
            if team_logo_path:
                logo_img = _load_logo(team_logo_path, TEAM_LOGO_SIZE)
                if logo_img:
                    card.paste(logo_img, (logo_center[0] - TEAM_LOGO_SIZE // 2, logo_center[1] - TEAM_LOGO_SIZE // 2), logo_img)
                else:
                    _draw_circle_avatar(
                        card,
                        logo_center,
                        TEAM_LOGO_SIZE,
                        (team.get("name", "?"))[:1].upper(),
                        fill_color=_to_rgba(theme["row_primary"]),
                        outline_color=_to_rgba(theme["card_border"]),
                        text_color=_to_rgba(theme["text_primary"]),
                    )
            else:
                _draw_circle_avatar(
                    card,
                    logo_center,
                    TEAM_LOGO_SIZE,
                    (team.get("name", "?"))[:1].upper(),
                    fill_color=_to_rgba(theme["row_primary"]),
                    outline_color=_to_rgba(theme["card_border"]),
                    text_color=_to_rgba(theme["text_primary"]),
                )
            x += COL_LOGO

            team_name = team.get("name", "Unknown")
            team_display = _truncate_text_to_width(
                card_draw,
                team_name,
                body_font,
                max_width=team_column_width - 16,
            ) or team_name[:24]
            text_w, text_h = _measure_text(card_draw, team_display, body_font)
            text_y = row_top + ROW_HEIGHT / 2 - text_h / 2
            card_draw.text((x + 8, text_y), team_display, fill=theme["text_primary"], font=body_font)
            x += team_column_width

            stats = [
                str(team.get("wins", 0)),
                str(team.get("losses", 0)),
                str(team.get("points_for", 0)),
                str(team.get("points_against", 0)),
                str(team.get("point_diff", 0)),
            ]
            widths = [COL_W, COL_L, COL_PF, COL_PA, COL_DIFF]
            for stat, width in zip(stats, widths):
                text_width, text_height = _measure_text(card_draw, stat, body_font)
                card_draw.text(
                    (x + width - text_width - 12, row_top + ROW_HEIGHT / 2 - text_height / 2),
                    stat,
                    fill=theme["text_stats"],
                    font=body_font,
                )
                x += width

            y += ROW_HEIGHT

        canvas.paste(card, (CANVAS_PADDING, CANVAS_PADDING), card)

        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        standings_image_cache.set(cache_key, image_bytes)
        buffer.seek(0)
        return buffer

    except Exception as exc:
        logger.error("Failed to generate standings image: %s", exc, exc_info=True)
        return None
