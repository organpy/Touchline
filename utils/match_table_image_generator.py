"""Match table rendering as a grid image showing all games (played and scheduled)."""

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

# Theme configuration (reused from standings)
BASE_THEME = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
    "text_primary": (245, 248, 255, 255),
    "text_muted": (173, 187, 212, 255),
    "cell_win": (34, 139, 34, 255),      # Green for wins
    "cell_loss": (139, 0, 0, 255),       # Dark red for losses
    "cell_pending": (60, 60, 80, 255),   # Gray for not played
    "cell_border": (40, 50, 70, 255),
}

LEAGUE_THEME_OVERRIDES = {
    "NBL": {
        "canvas_gradient": ((45, 30, 18), (95, 60, 28)),
        "card_bg": (52, 35, 20, 255),
        "card_border": (111, 74, 38, 255),
        "header_gradient": ((192, 142, 86), (126, 74, 33)),
        "text_primary": (255, 250, 240, 255),
        "text_muted": (236, 210, 160, 255),
        "cell_win": (46, 125, 50, 255),
        "cell_loss": (183, 28, 28, 255),
        "cell_pending": (75, 55, 35, 255),
        "cell_border": (100, 70, 40, 255),
    },
    "MBL": {
        "canvas_gradient": ((29, 6, 6), (70, 21, 2)),
        "header_gradient": ((255, 93, 32), (168, 48, 13)),
        "card_bg": (42, 24, 18, 255),
        "card_border": (84, 47, 34, 255),
        "cell_win": (56, 142, 60, 255),
        "cell_loss": (211, 47, 47, 255),
        "cell_pending": (60, 40, 35, 255),
        "cell_border": (90, 55, 40, 255),
    },
    "NCBA": {
        "canvas_gradient": ((5, 24, 24), (9, 58, 70)),
        "header_gradient": ((0, 173, 181), (0, 119, 182)),
        "card_bg": (14, 34, 44, 255),
        "card_border": (22, 64, 78, 255),
        "cell_win": (0, 150, 136, 255),
        "cell_loss": (244, 67, 54, 255),
        "cell_pending": (25, 50, 60, 255),
        "cell_border": (35, 70, 85, 255),
    },
}

MATCH_TABLE_CACHE_TTL_SECONDS = 120


def _to_rgba(color: Tuple[int, ...]) -> Tuple[int, int, int, int]:
    if len(color) == 4:
        return color
    if len(color) == 3:
        return (color[0], color[1], color[2], 255)
    raise ValueError("Color tuples must be length 3 or 4")


def _get_theme(league_code: Optional[str]) -> Dict[str, Any]:
    theme = {**BASE_THEME}
    if league_code:
        overrides = LEAGUE_THEME_OVERRIDES.get(league_code.upper())
        if overrides:
            theme.update(overrides)
    return theme


# Layout constants
CANVAS_PADDING = 32
CARD_CORNER_RADIUS = 24
HEADER_HEIGHT = 100
CELL_SIZE = 70
LOGO_COLUMN_WIDTH = 80
LOGO_SIZE = 50
CELL_PADDING = 4


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


match_table_cache = TTLCache(ttl_seconds=MATCH_TABLE_CACHE_TTL_SECONDS, max_size=32)


def clear_match_table_cache() -> None:
    """Clear cached match table images."""
    match_table_cache.clear()


def _make_cache_key(
    matches: List[Dict[str, Any]],
    teams: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str],
) -> str:
    payload = {
        "league_code": (league_code or "").upper(),
        "league_name": league_name,
        "season_name": season_name,
        "matches": matches,
        "teams": [t.get('id') for t in teams],
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
        title_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 24)
        subtitle_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 14)
        score_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 14)
        score_label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 10)
    except Exception:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        score_font = ImageFont.load_default()
        score_label_font = ImageFont.load_default()
    return title_font, subtitle_font, score_font, score_label_font


def generate_match_table_image(
    matches: List[Dict[str, Any]],
    teams: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
) -> Optional[io.BytesIO]:
    """
    Generate a match table grid image showing all games.
    
    Args:
        matches: List of match dicts with team1_id, team2_id, team1_score, team2_score, status
        teams: List of team dicts in the division
        season_name: Season name for title
        league_name: League name for title
        league_code: League code for theming (NBL, MBL, NCBA)
    
    Returns:
        BytesIO buffer with PNG image, or None on error
    """
    try:
        if not teams:
            return None

        cache_key = _make_cache_key(matches, teams, season_name, league_name, league_code)
        cached_image = match_table_cache.get(cache_key)
        if cached_image is not None:
            logger.debug("Match table image cache hit for key %s", cache_key[:8])
            return _bytes_to_buffer(cached_image)

        theme = _get_theme(league_code)
        title_font, subtitle_font, score_font, score_label_font = _load_fonts()

        num_teams = len(teams)
        
        # Build team ID to index mapping
        team_by_id = {t['id']: t for t in teams}
        team_indices = {t['id']: i for i, t in enumerate(teams)}

        # Build match lookup: (team1_id, team2_id) -> match
        match_lookup = {}
        for match in matches:
            t1_id = match.get('team1_id')
            t2_id = match.get('team2_id')
            if t1_id and t2_id:
                match_lookup[(t1_id, t2_id)] = match

        # Calculate dimensions
        grid_width = LOGO_COLUMN_WIDTH + (num_teams * CELL_SIZE)
        grid_height = LOGO_COLUMN_WIDTH + (num_teams * CELL_SIZE)  # Top row for logos too
        
        card_width = grid_width + (CANVAS_PADDING * 2)
        card_height = HEADER_HEIGHT + grid_height + CANVAS_PADDING
        
        canvas_width = card_width + (CANVAS_PADDING * 2)
        canvas_height = card_height + (CANVAS_PADDING * 2)

        # Create canvas with gradient background
        canvas = Image.new("RGBA", (canvas_width, canvas_height), _to_rgba(theme["canvas_gradient"][0]))
        canvas_draw = ImageDraw.Draw(canvas)
        _draw_vertical_gradient(canvas_draw, (0, 0, canvas_width, canvas_height), *theme["canvas_gradient"])

        # Create card
        card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle(
            (0, 0, card_width, card_height),
            radius=CARD_CORNER_RADIUS,
            fill=_to_rgba(theme["card_bg"]),
            outline=_to_rgba(theme["card_border"]),
            width=2,
        )

        # Draw header with gradient
        header_bbox = (0, 0, card_width, HEADER_HEIGHT)
        header_img = Image.new("RGBA", (card_width, HEADER_HEIGHT), (0, 0, 0, 0))
        header_draw = ImageDraw.Draw(header_img)
        
        # Rounded top corners only
        header_draw.rounded_rectangle(
            (0, 0, card_width, HEADER_HEIGHT + CARD_CORNER_RADIUS),
            radius=CARD_CORNER_RADIUS,
            fill=(0, 0, 0, 0),
        )
        _draw_vertical_gradient(header_draw, (0, 0, card_width, HEADER_HEIGHT), *theme["header_gradient"])
        
        # Mask to get rounded top corners
        mask = Image.new("L", (card_width, HEADER_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, card_width, HEADER_HEIGHT + CARD_CORNER_RADIUS),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        card.paste(header_img, (0, 0), mask)

        # Draw title text
        title_text = f"{league_name} — {season_name}"
        subtitle_text = "Match Results Grid"
        
        title_bbox = card_draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (card_width - title_width) // 2
        title_y = 25
        card_draw.text((title_x, title_y), title_text, fill=_to_rgba(theme["text_primary"]), font=title_font)

        subtitle_bbox = card_draw.textbbox((0, 0), subtitle_text, font=subtitle_font)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_x = (card_width - subtitle_width) // 2
        subtitle_y = title_y + 35
        card_draw.text((subtitle_x, subtitle_y), subtitle_text, fill=_to_rgba(theme["text_muted"]), font=subtitle_font)

        # Grid starting position
        grid_x = CANVAS_PADDING
        grid_y = HEADER_HEIGHT

        # Draw top row logos (column headers)
        for col, team in enumerate(teams):
            x = grid_x + LOGO_COLUMN_WIDTH + (col * CELL_SIZE)
            y = grid_y
            
            # Cell background
            cell_rect = (x, y, x + CELL_SIZE, y + LOGO_COLUMN_WIDTH)
            card_draw.rectangle(cell_rect, fill=_to_rgba(theme["card_bg"]), outline=_to_rgba(theme["cell_border"]))
            
            # Load and draw team logo
            logo_path = _find_logo_path(team.get('code'), team.get('name'))
            if logo_path:
                logo = _load_logo(logo_path, LOGO_SIZE)
                if logo:
                    logo_x = x + (CELL_SIZE - LOGO_SIZE) // 2
                    logo_y = y + (LOGO_COLUMN_WIDTH - LOGO_SIZE) // 2
                    card.paste(logo, (logo_x, logo_y), logo)

        # Draw left column logos (row headers)
        for row, team in enumerate(teams):
            x = grid_x
            y = grid_y + LOGO_COLUMN_WIDTH + (row * CELL_SIZE)
            
            # Cell background
            cell_rect = (x, y, x + LOGO_COLUMN_WIDTH, y + CELL_SIZE)
            card_draw.rectangle(cell_rect, fill=_to_rgba(theme["card_bg"]), outline=_to_rgba(theme["cell_border"]))
            
            # Load and draw team logo
            logo_path = _find_logo_path(team.get('code'), team.get('name'))
            if logo_path:
                logo = _load_logo(logo_path, LOGO_SIZE)
                if logo:
                    logo_x = x + (LOGO_COLUMN_WIDTH - LOGO_SIZE) // 2
                    logo_y = y + (CELL_SIZE - LOGO_SIZE) // 2
                    card.paste(logo, (logo_x, logo_y), logo)

        # Draw match cells
        for row, row_team in enumerate(teams):
            for col, col_team in enumerate(teams):
                x = grid_x + LOGO_COLUMN_WIDTH + (col * CELL_SIZE)
                y = grid_y + LOGO_COLUMN_WIDTH + (row * CELL_SIZE)
                
                cell_rect = (x, y, x + CELL_SIZE, y + CELL_SIZE)
                
                # Same team = diagonal (dark cell)
                if row_team['id'] == col_team['id']:
                    card_draw.rectangle(cell_rect, fill=_to_rgba(theme["cell_border"]), outline=_to_rgba(theme["cell_border"]))
                    continue
                
                # Look up match: row team is "home" (team1), col team is "away" (team2)
                match = match_lookup.get((row_team['id'], col_team['id']))
                
                if match and match.get('status') == 'final':
                    score1 = match.get('team1_score', 0) or 0
                    score2 = match.get('team2_score', 0) or 0
                    
                    # Determine win/loss from row team's perspective (row team = team1)
                    if score1 > score2:
                        cell_color = theme["cell_win"]
                    else:
                        cell_color = theme["cell_loss"]
                    
                    card_draw.rectangle(cell_rect, fill=_to_rgba(cell_color), outline=_to_rgba(theme["cell_border"]))
                    
                    # Draw score centered in cell
                    score_text = f"{score1}-{score2}"
                    score_bbox = card_draw.textbbox((0, 0), score_text, font=score_font)
                    score_width = score_bbox[2] - score_bbox[0]
                    score_height = score_bbox[3] - score_bbox[1]
                    score_x = x + (CELL_SIZE - score_width) // 2
                    score_y = y + (CELL_SIZE - score_height) // 2
                    card_draw.text((score_x, score_y), score_text, fill=_to_rgba(theme["text_primary"]), font=score_font)
                else:
                    # Not played yet
                    card_draw.rectangle(cell_rect, fill=_to_rgba(theme["cell_pending"]), outline=_to_rgba(theme["cell_border"]))

        # Paste card onto canvas
        canvas.paste(card, (CANVAS_PADDING, CANVAS_PADDING), card)

        # Save to buffer
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        image_bytes = buffer.getvalue()

        # Cache the result
        match_table_cache.set(cache_key, image_bytes)

        return _bytes_to_buffer(image_bytes)

    except Exception as e:
        logger.error("Error generating match table image: %s", e, exc_info=True)
        return None
