"""Simple standings image - compact view with just scores (W, L, PF, PA, Diff)."""

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

# Reuse theme system from standings_image_generator
BASE_THEME = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
    "header_overlay": None,
    "header_title_color": (245, 248, 255, 255),
    "header_subtitle_color": (173, 187, 212, 255),
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
        "header_subtitle_color": (255, 227, 176, 255),
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
        "canvas_gradient": ((24, 12, 2), (68, 38, 6)),
        "header_gradient": ((255, 150, 2), (191, 105, 2)),
        "row_primary": (58, 36, 10, 255),
        "row_alt": (66, 40, 12, 255),
        "row_divider": (120, 80, 20, 255),
        "card_bg": (48, 30, 8, 255),
        "card_border": (128, 78, 12, 255),
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

# Compact layout constants - standardized sizing (NCBA baseline)
CANVAS_PADDING = 36
CARD_CORNER_RADIUS = 28
HEADER_BLOCK_HEIGHT = 90
HEADER_SIDE_PADDING = 24
ROW_HEIGHT = 56
HEADER_HEIGHT = 44
INNER_MARGIN_X = 32
TABLE_MARGIN_TOP = 20
TABLE_MARGIN_BOTTOM = 28

# Compact column widths - fixed for all leagues (wider stats area)
COL_RANK = 54
COL_LOGO = 54
COL_TEAM = 220  # Fixed width for all leagues
COL_W = 56
COL_L = 56
COL_PF = 70
COL_PA = 70
COL_DIFF = 80

TEAM_LOGO_SIZE = 36
LEAGUE_LOGO_SIZE = 56

simple_standings_cache = TTLCache(ttl_seconds=120, max_size=32)


def _to_rgba(color: Tuple[int, ...]) -> Tuple[int, int, int, int]:
    if len(color) == 4:
        return color
    if len(color) == 3:
        return (color[0], color[1], color[2], 255)
    raise ValueError("Color tuples must be length 3 or 4")


def _get_theme(league_code: Optional[str]) -> Dict[str, Any]:
    theme = {**BASE_THEME}
    theme["rank_accent"] = BASE_THEME["rank_accent"].copy()
    if league_code:
        overrides = LEAGUE_THEME_OVERRIDES.get(league_code.upper())
        if overrides:
            if "rank_accent" in overrides:
                theme["rank_accent"] = overrides["rank_accent"].copy()
            theme.update({k: v for k, v in overrides.items() if k != "rank_accent"})
    return theme


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
        image.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - image.width) // 2, (size - image.height) // 2)
        canvas.paste(image, offset, image)
        return canvas
    except Exception:
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
        title_font = ImageFont.truetype(FONTS_DIR / "Poppins-Bold.ttf", 22)
        subtitle_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 13)
        header_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 12)
        body_font = ImageFont.truetype(FONTS_DIR / "Poppins-Regular.ttf", 12)
    except Exception:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        header_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    return title_font, subtitle_font, header_font, body_font


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if not text:
        return ""
    width, _ = _measure_text(draw, text.strip(), font)
    if width <= max_width:
        return text.strip()
    trimmed = text.strip()
    while trimmed:
        trimmed = trimmed[:-1]
        if _measure_text(draw, f"{trimmed}…", font)[0] <= max_width:
            return f"{trimmed}…"
    return "…"


def _draw_circle_avatar(base, center, diameter, text, *, fill_color, outline_color, text_color):
    draw = ImageDraw.Draw(base)
    radius = diameter // 2
    x0, y0 = center[0] - radius, center[1] - radius
    x1, y1 = center[0] + radius, center[1] + radius
    draw.ellipse((x0, y0, x1, y1), fill=fill_color, outline=outline_color, width=2)
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((center[0] - w / 2, center[1] - h / 2), text, fill=text_color, font=font)


def generate_simple_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
    recent_results: Optional[Dict[str, List[str]]] = None,
) -> Optional[io.BytesIO]:
    """Generate compact standings image.
    standings: list of dicts with keys: team_id, team_name, wins, losses, pf, pa, diff
    recent_results: optional dict mapping team_id -> list of last 5 markers ['W'|'L'|'N']
    Returns PNG bytes.
    """
    try:
        if not standings_data:
            return None

        # Cache key
        payload = {
            "type": "simple",
            "league_code": (league_code or "").upper(),
            "league_name": league_name,
            "season_name": season_name,
            "standings": standings_data,
            "recent_results": recent_results or {},
        }
        cache_key = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        
        cached = simple_standings_cache.get(cache_key)
        if cached is not None:
            buf = io.BytesIO(cached)
            buf.seek(0)
            return buf

        theme = _get_theme(league_code)

        # Fixed team column width for all leagues (standardized sizing)
        team_column_width = COL_TEAM

        # Table dimensions - extra space for stats and last 5 markers
        recent_width = 130 if recent_results else 0  # More space for markers
        table_width = COL_RANK + COL_LOGO + team_column_width + COL_W + COL_L + COL_PF + COL_PA + COL_DIFF + recent_width
        table_height = HEADER_HEIGHT + ROW_HEIGHT * len(standings_data)
        
        card_width = table_width + (INNER_MARGIN_X * 2)
        card_height = HEADER_BLOCK_HEIGHT + TABLE_MARGIN_TOP + table_height + TABLE_MARGIN_BOTTOM
        
        canvas_width = card_width + CANVAS_PADDING * 2
        canvas_height = card_height + CANVAS_PADDING * 2

        # Create canvas
        canvas = Image.new("RGBA", (canvas_width, canvas_height), _to_rgba(theme["canvas_gradient"][0]))
        canvas_draw = ImageDraw.Draw(canvas)
        _draw_vertical_gradient(canvas_draw, (0, 0, canvas_width, canvas_height), *theme["canvas_gradient"])

        # Create card
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

        # Header with gradient
        header_width = card_width - (HEADER_SIDE_PADDING * 2)
        header_x = HEADER_SIDE_PADDING
        header_y = 8

        header_layer = Image.new("RGBA", (header_width, HEADER_BLOCK_HEIGHT), (0, 0, 0, 0))
        header_layer_draw = ImageDraw.Draw(header_layer)
        _draw_vertical_gradient(header_layer_draw, (0, 0, header_width, HEADER_BLOCK_HEIGHT), *theme["header_gradient"])

        header_overlay = theme.get("header_overlay")
        if header_overlay:
            header_layer_draw.rectangle((0, 0, header_width, HEADER_BLOCK_HEIGHT), fill=header_overlay)

        # Round top corners of header
        header_mask = Image.new("L", (header_width, HEADER_BLOCK_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(header_mask)
        mask_draw.rounded_rectangle(
            (0, 0, header_width, HEADER_BLOCK_HEIGHT + CARD_CORNER_RADIUS),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        card.paste(header_layer, (header_x, header_y), header_mask)

        # League logo
        league_logo = _find_logo_path(league_code, league_name)
        logo_y = header_y + (HEADER_BLOCK_HEIGHT - LEAGUE_LOGO_SIZE) // 2
        logo_x = header_x + 12
        if league_logo:
            logo_img = _load_logo(league_logo, LEAGUE_LOGO_SIZE)
            if logo_img:
                card.paste(logo_img, (logo_x, logo_y), logo_img)

        # Title and subtitle
        text_x = logo_x + LEAGUE_LOGO_SIZE + 16
        header_center_y = header_y + HEADER_BLOCK_HEIGHT // 2
        
        card_draw.text((text_x, header_center_y - 18), league_name, fill=theme["header_title_color"], font=title_font)
        
        subtitle = f"{(season_name or 'Season').upper()} • {len(standings_data)} CLUBS"
        card_draw.text((text_x, header_center_y + 6), subtitle, fill=theme["header_subtitle_color"], font=subtitle_font)

        # Table header
        table_x = INNER_MARGIN_X
        table_y = HEADER_BLOCK_HEIGHT + TABLE_MARGIN_TOP
        card_draw.rectangle((table_x, table_y, table_x + table_width, table_y + HEADER_HEIGHT), fill=theme["row_primary"])

        columns = [
            ("", COL_RANK),
            ("", COL_LOGO),
            ("Team", team_column_width),
            ("W", COL_W),
            ("L", COL_L),
            ("PF", COL_PF),
            ("PA", COL_PA),
            ("±", COL_DIFF),
        ]
        if recent_results:
            columns.append(("Last 5", recent_width))

        x = table_x
        for text, width in columns:
            if text:
                text_w, text_h = _measure_text(card_draw, text, header_font)
                text_x = x + width / 2 - text_w / 2
                if text.lower() == "team":
                    text_x = x + 8
                card_draw.text((text_x, table_y + HEADER_HEIGHT / 2 - text_h / 2), text, fill=theme["text_muted"], font=header_font)
            x += width

        # Data rows
        y = table_y + HEADER_HEIGHT
        def _marker_color(m: str) -> Optional[Tuple[int, int, int, int]]:
            """Return fill color for marker. None means outline-only (not reported)."""
            if m == 'W':
                return _to_rgba((46, 204, 113))  # Green for win
            if m == 'L':
                return _to_rgba((231, 76, 60))   # Red for loss
            return None  # No fill - draw outline only for unreported

        for rank, team in enumerate(standings_data, 1):
            row_top = y
            row_bottom = y + ROW_HEIGHT
            row_color = theme["row_primary"] if rank % 2 else theme["row_alt"]
            card_draw.rectangle((table_x, row_top, table_x + table_width, row_bottom), fill=row_color)
            card_draw.line((table_x, row_bottom, table_x + table_width, row_bottom), fill=theme["row_divider"], width=1)

            # Rank accent bar
            rank_accent = theme["rank_accent"].get(rank)
            if rank_accent:
                card_draw.rectangle((table_x, row_top, table_x + 4, row_bottom), fill=rank_accent)

            x = table_x

            # Rank
            rank_text = str(rank)
            tw, th = _measure_text(card_draw, rank_text, body_font)
            card_draw.text((x + COL_RANK / 2 - tw / 2, row_top + ROW_HEIGHT / 2 - th / 2), rank_text, fill=theme["text_primary"], font=body_font)
            x += COL_RANK

            # Logo
            team_logo_path = _find_logo_path(team.get("code"), team.get("name"))
            logo_center = (int(x + COL_LOGO / 2), int(row_top + ROW_HEIGHT / 2))
            if team_logo_path:
                logo_img = _load_logo(team_logo_path, TEAM_LOGO_SIZE)
                if logo_img:
                    card.paste(logo_img, (logo_center[0] - TEAM_LOGO_SIZE // 2, logo_center[1] - TEAM_LOGO_SIZE // 2), logo_img)
                else:
                    _draw_circle_avatar(card, logo_center, TEAM_LOGO_SIZE, team.get("name", "?")[:1].upper(),
                                        fill_color=_to_rgba(theme["row_primary"]), outline_color=_to_rgba(theme["card_border"]),
                                        text_color=_to_rgba(theme["text_primary"]))
            else:
                _draw_circle_avatar(card, logo_center, TEAM_LOGO_SIZE, team.get("name", "?")[:1].upper(),
                                    fill_color=_to_rgba(theme["row_primary"]), outline_color=_to_rgba(theme["card_border"]),
                                    text_color=_to_rgba(theme["text_primary"]))
            x += COL_LOGO

            # Team name
            team_name = _truncate_text(card_draw, team.get("name", "Unknown"), body_font, team_column_width - 16)
            tw, th = _measure_text(card_draw, team_name, body_font)
            card_draw.text((x + 8, row_top + ROW_HEIGHT / 2 - th / 2), team_name, fill=theme["text_primary"], font=body_font)
            x += team_column_width

            # Stats (W, L, PF, PA, Diff)
            stats = [
                str(team.get("wins", 0)),
                str(team.get("losses", 0)),
                str(team.get("points_for", 0)),
                str(team.get("points_against", 0)),
                f"{team.get('point_diff', 0):+d}" if team.get("point_diff", 0) != 0 else "0",
            ]
            widths = [COL_W, COL_L, COL_PF, COL_PA, COL_DIFF]
            
            for stat, width in zip(stats, widths):
                tw, th = _measure_text(card_draw, stat, body_font)
                card_draw.text((x + width / 2 - tw / 2, row_top + ROW_HEIGHT / 2 - th / 2), stat, fill=theme["text_stats"], font=body_font)
                x += width

            # Recent results markers (last 5 games)
            if recent_results:
                markers = None
                # team id could be under different keys depending on source
                team_id = team.get('team_id') or team.get('id') or team.get('teams.id')
                if team_id is not None:
                    markers = (recent_results or {}).get(str(team_id)) or (recent_results or {}).get(team_id)
                if markers:
                    cx = x + 16
                    cy = row_top + ROW_HEIGHT / 2
                    for m in markers[:5]:
                        r = 8  # Slightly bigger markers
                        fill_color = _marker_color(m)
                        if fill_color:
                            # Win or Loss - filled circle
                            card_draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill_color)
                        else:
                            # Not reported - dashed outline circle (hollow)
                            outline_color = _to_rgba((100, 100, 100))
                            card_draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=outline_color, width=2)
                        cx += 24  # More spacing between markers
                x += recent_width

            y += ROW_HEIGHT

        # Paste card onto canvas
        canvas.paste(card, (CANVAS_PADDING, CANVAS_PADDING), card)

        # Save
        buffer = io.BytesIO()
        canvas.convert("RGB").save(buffer, format="PNG")
        image_bytes = buffer.getvalue()

        simple_standings_cache.set(cache_key, image_bytes)

        buffer = io.BytesIO(image_bytes)
        buffer.seek(0)
        return buffer

    except Exception as e:
        logger.error(f"Failed to generate simple standings: {e}", exc_info=True)
        return None
