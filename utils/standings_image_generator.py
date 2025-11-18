"""Modern standings rendering with PNG logos and gradients."""

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

# Color palette
COLOR_BACKGROUND = (239, 242, 247, 255)
COLOR_CARD = (255, 255, 255, 255)
COLOR_TABLE_HEADER_GRADIENT = ((38, 92, 167), (71, 131, 218))
COLOR_TABLE_TEXT = (32, 41, 56)
COLOR_SECONDARY_TEXT = (120, 139, 161)
COLOR_DIVIDER = (225, 232, 240)
COLOR_RANK_ACCENTS = {
    1: (255, 201, 61),  # Gold
    2: (207, 207, 207),  # Silver
    3: (205, 147, 79),   # Bronze
}

# Layout constants
CARD_PADDING = 28
HEADER_BLOCK_HEIGHT = 110
ROW_HEIGHT = 56
HEADER_HEIGHT = 46
COL_RANK = 70
COL_LOGO = 64
COL_TEAM = 210
COL_W = 52
COL_L = 52
COL_PF = 64
COL_PA = 64
COL_DIFF = 70
TEAM_LOGO_SIZE = 40
LEAGUE_LOGO_SIZE = 64
FOOTER_HEIGHT = 40


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


def _draw_gradient(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], start_color, end_color):
    x0, y0, x1, y1 = bbox
    height = y1 - y0
    for i in range(height):
        ratio = i / max(height - 1, 1)
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


def _draw_circle_avatar(base: Image.Image, center: Tuple[int, int], diameter: int, text: str):
    draw = ImageDraw.Draw(base)
    radius = diameter // 2
    x0, y0 = center[0] - radius, center[1] - radius
    x1, y1 = center[0] + radius, center[1] + radius
    draw.ellipse((x0, y0, x1, y1), fill=(230, 236, 245), outline=(200, 208, 220))
    text_font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=text_font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((center[0] - w / 2, center[1] - h / 2), text, fill=(80, 95, 115), font=text_font)


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
        card_width = table_width + CARD_PADDING * 2
        table_height = HEADER_HEIGHT + ROW_HEIGHT * len(standings_data)
        card_height = HEADER_BLOCK_HEIGHT + table_height + FOOTER_HEIGHT + CARD_PADDING * 2

        canvas = Image.new("RGBA", (card_width, card_height), COLOR_BACKGROUND)
        card = Image.new("RGBA", (card_width - CARD_PADDING * 2, card_height - CARD_PADDING * 2), COLOR_CARD)
        canvas.paste(card, (CARD_PADDING, CARD_PADDING))

        card_draw = ImageDraw.Draw(card)
        title_font, subtitle_font, header_font, body_font = _load_fonts()

        # Header block with gradient
        header_bbox = (0, 0, card.width, HEADER_BLOCK_HEIGHT)
        _draw_gradient(card_draw, header_bbox, *COLOR_TABLE_HEADER_GRADIENT)

        league_logo = _find_logo_path(league_code, league_name)
        if league_logo:
            logo_img = _load_logo(league_logo, LEAGUE_LOGO_SIZE)
        else:
            logo_img = None

        if logo_img:
            card.paste(logo_img, (32, 23), logo_img)
        else:
            _draw_circle_avatar(card, (32 + LEAGUE_LOGO_SIZE // 2, HEADER_BLOCK_HEIGHT // 2), LEAGUE_LOGO_SIZE, (league_code or "L")[:2].upper())

        card_draw.text((32 + LEAGUE_LOGO_SIZE + 20, 28), league_name, fill=(255, 255, 255), font=title_font)
        card_draw.text((32 + LEAGUE_LOGO_SIZE + 20, 68), f"Season {season_name}", fill=(235, 240, 248), font=subtitle_font)

        # Table header
        table_y = HEADER_BLOCK_HEIGHT + 20
        header_y = table_y
        card_draw.rectangle((32, header_y, 32 + table_width, header_y + HEADER_HEIGHT), fill=(248, 249, 252))

        columns = [
            ("Rk", COL_RANK),
            ("", COL_LOGO),
            ("Team", COL_TEAM),
            ("W", COL_W),
            ("L", COL_L),
            ("PF", COL_PF),
            ("PA", COL_PA),
            ("Diff", COL_DIFF),
        ]

        x = 32
        for text, width in columns:
            if text:
                card_draw.text((x + width / 2 - 10, header_y + HEADER_HEIGHT / 2 - 8), text, fill=COLOR_SECONDARY_TEXT, font=header_font)
            x += width

        # Rows
        y = header_y + HEADER_HEIGHT
        for rank, team in enumerate(standings_data, 1):
            row_top = y
            row_bottom = y + ROW_HEIGHT
            row_color = (255, 255, 255) if rank % 2 else (248, 250, 253)
            card_draw.rectangle((32, row_top, 32 + table_width, row_bottom), fill=row_color)

            if rank in COLOR_RANK_ACCENTS:
                card_draw.rectangle((32, row_top, 32 + table_width, row_top + 4), fill=COLOR_RANK_ACCENTS[rank])

            x = 32
            rank_label = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))
            card_draw.text((x + 18, row_top + ROW_HEIGHT / 2 - 10), rank_label, fill=COLOR_TABLE_TEXT, font=body_font)
            x += COL_RANK

            # Team logo
            team_logo_path = _find_logo_path(team.get("code"), team.get("name"))
            logo_center = (int(x + COL_LOGO / 2), int(row_top + ROW_HEIGHT / 2))
            if team_logo_path:
                logo_img = _load_logo(team_logo_path, TEAM_LOGO_SIZE)
                if logo_img:
                    card.paste(logo_img, (logo_center[0] - TEAM_LOGO_SIZE // 2, logo_center[1] - TEAM_LOGO_SIZE // 2), logo_img)
                else:
                    _draw_circle_avatar(card, logo_center, TEAM_LOGO_SIZE, (team.get("name", "?"))[:1].upper())
            else:
                _draw_circle_avatar(card, logo_center, TEAM_LOGO_SIZE, (team.get("name", "?"))[:1].upper())
            x += COL_LOGO

            # Team name
            card_draw.text((x, row_top + ROW_HEIGHT / 2 - 10), team.get("name", "Unknown")[:22], fill=COLOR_TABLE_TEXT, font=body_font)
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
                text_width, _ = _measure_text(card_draw, stat, body_font)
                card_draw.text((x + width - text_width - 10, row_top + ROW_HEIGHT / 2 - 10), stat, fill=COLOR_SECONDARY_TEXT, font=body_font)
                x += width

            y += ROW_HEIGHT

        # Footer text
        footer_text = "W = Wins • L = Losses • PF = Points For • PA = Points Against • Diff = Point Differential"
        card_draw.text((32, card.height - FOOTER_HEIGHT), footer_text, fill=COLOR_SECONDARY_TEXT, font=body_font)

        # Merge card back onto canvas
        canvas.paste(card, (CARD_PADDING, CARD_PADDING))

        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    except Exception as exc:
        logger.error("Failed to generate standings image: %s", exc, exc_info=True)
        return None
