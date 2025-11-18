"""Modern standings rendering with PNG logos, dark canvas, and gradients."""

from __future__ import annotations

import io
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT_DIR / "images"

BASE_THEME = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
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
        "canvas_gradient": ((255, 247, 223), (247, 208, 128)),
        "card_bg": (255, 252, 245, 255),
        "card_border": (240, 200, 132, 255),
        "header_gradient": ((255, 212, 124), (255, 166, 74)),
        "text_primary": (48, 32, 12, 255),
        "text_muted": (122, 95, 54, 255),
        "text_stats": (94, 68, 32, 255),
        "row_primary": (255, 242, 214, 255),
        "row_alt": (255, 235, 198, 255),
        "row_divider": (233, 198, 142, 255),
        "rank_accent": {
            1: (255, 210, 92, 255),
            2: (212, 198, 182, 255),
            3: (245, 182, 109, 255),
        },
    },
    "MBL": {
        "canvas_gradient": ((29, 6, 6), (70, 21, 2)),
        "header_gradient": ((255, 93, 32), (168, 48, 13)),
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
        return image.resize((size, size), Image.LANCZOS)
    except Exception:
        logger.warning("Failed to load logo %s", path)
        return None


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
        title_font = ImageFont.truetype("arial.ttf", 28)
        subtitle_font = ImageFont.truetype("arial.ttf", 16)
        header_font = ImageFont.truetype("arial.ttf", 14)
        body_font = ImageFont.truetype("arial.ttf", 13)
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


def generate_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
) -> Optional[io.BytesIO]:
    try:
        if not standings_data:
            return None

        theme = _get_theme(league_code)

        table_width = (
            COL_RANK
            + COL_LOGO
            + COL_TEAM
            + COL_W
            + COL_L
            + COL_PF
            + COL_PA
            + COL_DIFF
        )
        table_height = HEADER_HEIGHT + ROW_HEIGHT * len(standings_data)
        card_width = table_width + (INNER_MARGIN_X * 2)
        card_height = HEADER_BLOCK_HEIGHT + TABLE_MARGIN_TOP + table_height + TABLE_MARGIN_BOTTOM
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

        header_bbox = (24, 24, card_width - 24, HEADER_BLOCK_HEIGHT)
        _draw_vertical_gradient(card_draw, header_bbox, *theme["header_gradient"])

        league_logo = _find_logo_path(league_code, league_name)
        logo_y = 24 + (HEADER_BLOCK_HEIGHT - LEAGUE_LOGO_SIZE) // 2
        if league_logo:
            logo_img = _load_logo(league_logo, LEAGUE_LOGO_SIZE)
        else:
            logo_img = None

        if logo_img:
            card.paste(logo_img, (40, logo_y), logo_img)
        else:
            _draw_circle_avatar(
                card,
                (40 + LEAGUE_LOGO_SIZE // 2, logo_y + LEAGUE_LOGO_SIZE // 2),
                LEAGUE_LOGO_SIZE,
                (league_code or "L")[:2].upper(),
                fill_color=_to_rgba(theme["row_primary"]),
                outline_color=_to_rgba(theme["card_border"]),
                text_color=_to_rgba(theme["text_primary"]),
            )

        text_x = 40 + LEAGUE_LOGO_SIZE + 28
        card_draw.text((text_x, logo_y + 10), league_name, fill=theme["text_primary"], font=title_font)
        card_draw.text((text_x, logo_y + 58), f"Season {season_name}", fill=theme["text_muted"], font=subtitle_font)

        table_x = INNER_MARGIN_X
        table_y = HEADER_BLOCK_HEIGHT + TABLE_MARGIN_TOP
        card_draw.rectangle((table_x, table_y, table_x + table_width, table_y + HEADER_HEIGHT), fill=theme["row_primary"])

        columns = [
            ("", COL_RANK),
            ("", COL_LOGO),
            ("Team", COL_TEAM),
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
                card_draw.text(
                    (x + width / 2 - text_w / 2, table_y + HEADER_HEIGHT / 2 - text_h / 2),
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

            card_draw.text((x, row_top + ROW_HEIGHT / 2 - 12), team.get("name", "Unknown")[:24], fill=theme["text_primary"], font=body_font)
            x += COL_TEAM

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
        buffer.seek(0)
        return buffer

    except Exception as exc:
        logger.error("Failed to generate standings image: %s", exc, exc_info=True)
        return None
