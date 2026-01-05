"""Player card image generator.

Design goal: match the standings-style visuals (gradient canvas + inner rounded card +
gradient header) and render a league-specific card type (NBL/MBL/NCBA).

The stat fields displayed are aligned with the Supabase schema (db/migrations/init.sql):
- points, goals, assists, saves, blocks, own_goals
- powerup_plays, powerups_used, powerup_percentage
"""

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
DATA_DIR = ROOT_DIR / "data"
FONTS_DIR = ROOT_DIR / "fonts"


# Theme configuration (mirrors utils/standings_image_generator.py)
BASE_THEME: Dict[str, Any] = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
    "text_primary": (245, 248, 255, 255),
    "text_muted": (173, 187, 212, 255),
    "accent_color": (90, 134, 255, 255),
    "row_primary": (35, 46, 67, 255),
    "row_alt": (40, 52, 76, 255),
    "row_divider": (56, 68, 94, 255),
}

LEAGUE_THEME_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "NBL": {
        "canvas_gradient": ((45, 30, 18), (95, 60, 28)),
        "card_bg": (52, 35, 20, 255),
        "card_border": (111, 74, 38, 255),
        "header_gradient": ((192, 142, 86), (126, 74, 33)),
        "text_primary": (255, 250, 240, 255),
        "text_muted": (236, 210, 160, 255),
        "accent_color": (247, 173, 68, 255),
        "row_primary": (64, 44, 24, 255),
        "row_alt": (55, 36, 20, 255),
        "row_divider": (122, 86, 44, 255),
    },
    "MBL": {
        "canvas_gradient": ((24, 12, 2), (68, 38, 6)),
        "header_gradient": ((255, 150, 2), (191, 105, 2)),
        "card_bg": (48, 30, 8, 255),
        "card_border": (128, 78, 12, 255),
        "accent_color": (255, 149, 0, 255),
        "row_primary": (58, 36, 10, 255),
        "row_alt": (66, 40, 12, 255),
        "row_divider": (120, 80, 20, 255),
    },
    "NCBA": {
        "canvas_gradient": ((5, 24, 24), (9, 58, 70)),
        "header_gradient": ((0, 173, 181), (0, 119, 182)),
        "card_bg": (14, 34, 44, 255),
        "card_border": (22, 64, 78, 255),
        "accent_color": (126, 199, 255, 255),
        "row_primary": (16, 46, 60, 255),
        "row_alt": (18, 54, 70, 255),
        "row_divider": (34, 92, 110, 255),
    },
}


# Layout constants (standings-style)
CANVAS_PADDING = 36
CARD_PADDING = 28
CARD_CORNER_RADIUS = 28
HEADER_BLOCK_HEIGHT = 86

CONTENT_WIDTH = 360
CONTENT_HEIGHT = 520

LEAGUE_LOGO_SIZE = 40

# Fonts
NAME_FONT_SIZE = 30
TEAM_FONT_SIZE = 14
SEASONS_FONT_SIZE = 13
HERO_VALUE_FONT_SIZE = 42
HERO_LABEL_FONT_SIZE = 13
STAT_VALUE_FONT_SIZE = 18
STAT_LABEL_FONT_SIZE = 11
SECTION_FONT_SIZE = 16
SMALL_FONT_SIZE = 10

PLAYER_CARD_CACHE_TTL_SECONDS = 600  


def sort_seasons(seasons_list: List[str]) -> List[str]:
    """Sort seasons by numeric order, not alphabetically."""
    season_order = {
        "S1": 1,
        "S2": 2,
        "S3": 3,
        "S4": 4,
        "S5": 5,
        "S6": 6,
        "S7": 7,
        "S8": 8,
        "S9": 9,
        "S10": 10,
        "S11": 11,
        "S12": 12,
    }
    return sorted(seasons_list, key=lambda s: season_order.get(s, 999))


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


@lru_cache(maxsize=1)
def _team_colors_index() -> Dict[str, Dict[str, Any]]:
    """Load team color palettes from data/team_colors.json if available."""
    path = DATA_DIR / "team_colors.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        out: Dict[str, Dict[str, Any]] = {}
        for code, palette in (data or {}).items():
            out[(code or "").upper()] = palette or {}
        return out
    except Exception:
        logger.warning("Failed to load team colors from %s", path)
        return {}


player_card_cache = TTLCache(ttl_seconds=PLAYER_CARD_CACHE_TTL_SECONDS, max_size=32)  # Reduced from 64 to 32 for memory optimization


def clear_player_card_cache() -> None:
    """Clear cached player card images."""
    player_card_cache.clear()


def _make_cache_key(
    player_id: str,
    player_name: str,
    team_code: Optional[str],
    league_code: Optional[str],
    stats: Dict[str, Any],
    season_name: Optional[str],
    seasons: List[str],
    awards: List[str],
    teams_played: List[str],
) -> str:
    payload = {
        "player_id": player_id,
        "player_name": player_name,
        "team_code": (team_code or "").upper(),
        "league_code": (league_code or "").upper(),
        "stats": stats,
        "season_name": (season_name or ""),
        "seasons": seasons,
        "awards": awards,
        "teams_played": teams_played,
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
        image.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - image.width) // 2, (size - image.height) // 2)
        canvas.paste(image, offset, image)
        return canvas
    except Exception:
        logger.warning("Failed to load logo %s", path)
        return None


def _draw_vertical_gradient(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], start, end) -> None:
    x0, y0, x1, y1 = bbox
    height = max(y1 - y0, 1)
    for i in range(height):
        p = i / height
        r = int(start[0] + (end[0] - start[0]) * p)
        g = int(start[1] + (end[1] - start[1]) * p)
        b = int(start[2] + (end[2] - start[2]) * p)
        draw.line([(x0, y0 + i), (x1, y0 + i)], fill=(r, g, b))


def _load_fonts():
    try:
        title_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 28)
        name_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), NAME_FONT_SIZE)
        team_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), TEAM_FONT_SIZE)
        seasons_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), SEASONS_FONT_SIZE)
        hero_value_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), HERO_VALUE_FONT_SIZE)
        hero_label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), HERO_LABEL_FONT_SIZE)
        stat_value_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), STAT_VALUE_FONT_SIZE)
        stat_label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), STAT_LABEL_FONT_SIZE)
        section_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), SECTION_FONT_SIZE)
        small_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), SMALL_FONT_SIZE)
    except Exception:
        title_font = ImageFont.load_default()
        name_font = ImageFont.load_default()
        team_font = ImageFont.load_default()
        seasons_font = ImageFont.load_default()
        hero_value_font = ImageFont.load_default()
        hero_label_font = ImageFont.load_default()
        stat_value_font = ImageFont.load_default()
        stat_label_font = ImageFont.load_default()
        section_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    return (
        title_font,
        name_font,
        team_font,
        seasons_font,
        hero_value_font,
        hero_label_font,
        stat_value_font,
        stat_label_font,
        section_font,
        small_font,
    )


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _center_x(container_width: int, content_width: int) -> int:
    return max((container_width - content_width) // 2, 0)


def _draw_stat_cell(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    label: str,
    value: str,
    theme: Dict[str, Any],
    value_font: ImageFont.ImageFont,
    label_font: ImageFont.ImageFont,
) -> None:
    draw.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=14,
        fill=_to_rgba(theme["row_alt"]),
        outline=_to_rgba(theme["row_divider"]),
        width=1,
    )

    value_w, value_h = _measure_text(draw, value, value_font)
    label_w, label_h = _measure_text(draw, label, label_font)

    value_x = x + (w - value_w) // 2
    value_y = y + (h // 2 - value_h) // 2  # center in top half
    label_x = x + (w - label_w) // 2
    label_y = y + h // 2 + (h // 2 - label_h) // 2  # center in bottom half

    draw.text((value_x, value_y), value, fill=_to_rgba(theme["text_primary"]), font=value_font)
    draw.text((label_x, label_y), label, fill=_to_rgba(theme["text_muted"]), font=label_font)


def _abbr_award(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())
    if not cleaned:
        return "AWD"
    if len(cleaned) <= 5:
        return cleaned
    return cleaned[:4]


def _draw_award_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    *,
    code: str,
    count: int,
    theme: Dict[str, Any],
    font: ImageFont.ImageFont,
    pad_x: int = 10,
    height: int = 28,
) -> int:
    label = f"x{count} {code}"
    tw, th = _measure_text(draw, label, font)
    w = tw + pad_x * 2
    draw.rounded_rectangle(
        (x, y, x + w, y + height),
        radius=12,
        fill=_to_rgba(theme["row_alt"]),
        outline=_to_rgba(theme["row_divider"]),
        width=1,
    )
    tx = x + (w - tw) // 2
    ty = y + (height - th) // 2
    draw.text((tx, ty), label, fill=_to_rgba(theme["text_primary"]), font=font)
    return w


def generate_player_card_image(
    player_id: str,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_code: Optional[str] = None,
    season_name: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    seasons: Optional[List[str]] = None,
    awards: Optional[List[str]] = None,
    teams_played: Optional[List[str]] = None,
) -> Optional[io.BytesIO]:
    """Generate a player card image.

    Args:
        player_id: Unique player identifier
        player_name: Player display name
        team_name: Team name (for display)
        team_code: Team code (for logo lookup)
        league_code: League code for theming (NBL, MBL, NCBA)
        stats: Dict with season totals aligned to schema
        seasons: List of seasons played
        awards: List of awards
        teams_played: List of teams played for

    Returns:
        BytesIO buffer with PNG image, or None on error
    """
    try:
        stats = stats or {}
        seasons = seasons or []
        awards = awards or []
        teams_played = teams_played or []

        league_code_norm = (league_code or "").upper() or None

        cache_key = _make_cache_key(
            player_id=player_id,
            player_name=player_name,
            team_code=team_code,
            league_code=league_code_norm,
            stats=stats,
            season_name=season_name,
            seasons=seasons,
            awards=awards,
            teams_played=teams_played,
        )
        cached_image = player_card_cache.get(cache_key)
        if cached_image is not None:
            return _bytes_to_buffer(cached_image)

        theme = _get_theme(league_code_norm)
        (
            title_font,
            name_font,
            team_font,
            seasons_font,
            hero_value_font,
            hero_label_font,
            stat_value_font,
            stat_label_font,
            section_font,
            small_font,
        ) = _load_fonts()

        # Derived stats (schema-aligned)
        games = int(stats.get("games_played", 0) or 0)
        points = int(stats.get("points", 0) or 0)
        goals = int(stats.get("goals", 0) or 0)
        assists = int(stats.get("assists", 0) or 0)
        saves = int(stats.get("saves", 0) or 0)
        blocks = int(stats.get("blocks", 0) or 0)
        own_goals = int(stats.get("own_goals", 0) or 0)
        pp_plays = int(stats.get("powerup_plays", 0) or 0)
        pp_used = int(stats.get("powerups_used", 0) or 0)
        pp_pct = stats.get("powerup_percentage")
        if pp_pct is None:
            pp_pct = round((pp_plays / pp_used) * 100, 2) if pp_used > 0 else 0
        try:
            pp_pct_val = float(pp_pct)
        except Exception:
            pp_pct_val = 0.0

        # Geometry
        card_width = CONTENT_WIDTH + (CARD_PADDING * 2)
        card_height = CONTENT_HEIGHT + (CARD_PADDING * 2)
        canvas_width = card_width + (CANVAS_PADDING * 2)
        canvas_height = card_height + (CANVAS_PADDING * 2)

        # Canvas
        canvas = Image.new("RGBA", (canvas_width, canvas_height), _to_rgba(theme["canvas_gradient"][0]))
        canvas_draw = ImageDraw.Draw(canvas)
        _draw_vertical_gradient(canvas_draw, (0, 0, canvas_width, canvas_height), *theme["canvas_gradient"])

        # Card
        card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
        card_draw = ImageDraw.Draw(card)
        card_draw.rounded_rectangle(
            (0, 0, card_width, card_height),
            radius=CARD_CORNER_RADIUS,
            fill=_to_rgba(theme["card_bg"]),
            outline=_to_rgba(theme["card_border"]),
            width=2,
        )

        # Header (gradient with rounded top corners) - full-width across card
        header_width = card_width
        header_x = 0
        header_y = 0

        header_layer = Image.new("RGBA", (header_width, HEADER_BLOCK_HEIGHT), (0, 0, 0, 0))
        header_layer_draw = ImageDraw.Draw(header_layer)
        _draw_vertical_gradient(header_layer_draw, (0, 0, header_width, HEADER_BLOCK_HEIGHT), *theme["header_gradient"])

        header_mask = Image.new("L", (header_width, HEADER_BLOCK_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(header_mask)
        mask_draw.rounded_rectangle(
            (0, 0, header_width, HEADER_BLOCK_HEIGHT + CARD_CORNER_RADIUS),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        card.paste(header_layer, (header_x, header_y), header_mask)

        # Header content: centered league logo + league text + season
        league_text = league_code_norm or "LEAGUE"
        league_logo_path = _find_logo_path(league_text)
        league_logo_img = _load_logo(league_logo_path, LEAGUE_LOGO_SIZE) if league_logo_path else None

        season_text = (f"Season {season_name.replace('S', '')}" if season_name else "").strip()
        if not season_text:
            season_text = ""

        # Measure text sizes with baseline accounting
        league_bbox = card_draw.textbbox((0, 0), league_text, font=title_font)
        league_width = league_bbox[2] - league_bbox[0]
        league_height = league_bbox[3] - league_bbox[1]
        league_offset = league_bbox[1]  # Top offset from baseline
        
        season_bbox = card_draw.textbbox((0, 0), season_text, font=section_font) if season_text else None
        season_width = (season_bbox[2] - season_bbox[0]) if season_bbox else 0
        season_height = (season_bbox[3] - season_bbox[1]) if season_bbox else 0
        season_offset = season_bbox[1] if season_bbox else 0  # Top offset from baseline
        
        text_block_width = max(league_width, season_width) if season_text else league_width

        # Combined width for centering
        has_logo = league_logo_img is not None
        group_gap = 10 if has_logo else 0
        combined_width = (LEAGUE_LOGO_SIZE + group_gap if has_logo else 0) + text_block_width
        combined_x = (header_width - combined_width) // 2
        header_center_y = HEADER_BLOCK_HEIGHT // 2

        # Vertically center the logo
        logo_x = combined_x
        logo_y = header_center_y - LEAGUE_LOGO_SIZE // 2
        
        # Position text to the right of logo
        text_x = logo_x + LEAGUE_LOGO_SIZE + group_gap
        
        # Position league text centered on logo's middle
        league_y = header_center_y - league_height // 2 - league_offset - 3
        
        # Position season text below league text with moderate gap
        line_gap = 20  # Increased from 16 for better spacing between league and season text
        season_y = league_y + league_height + line_gap - season_offset - 3

        if league_logo_img:
            card.paste(league_logo_img, (logo_x, logo_y), league_logo_img)

        card_draw.text((text_x, league_y), league_text, fill=_to_rgba(theme["text_primary"]), font=title_font)
        
        if season_text:
            card_draw.text((text_x, season_y), season_text, fill=_to_rgba(theme["text_primary"]), font=section_font)

        # Body
        cx = CARD_PADDING
        cw = card_width - (CARD_PADDING * 2)
        y = HEADER_BLOCK_HEIGHT + 18

        # Player name
        name_w, name_h = _measure_text(card_draw, player_name, name_font)
        card_draw.text((cx + (cw - name_w) // 2, y), player_name, fill=_to_rgba(theme["text_primary"]), font=name_font)
        y += name_h + 18
        display_team = (team_name or "Free Agent").strip() or "Free Agent"
        team_logo_path = _find_logo_path(team_code, display_team)
        team_logo_img = _load_logo(team_logo_path, 20) if team_logo_path else None

        # Measure team text with baseline accounting
        team_bbox = card_draw.textbbox((0, 0), display_team, font=team_font)
        team_width = team_bbox[2] - team_bbox[0]
        team_height = team_bbox[3] - team_bbox[1]
        team_offset = team_bbox[1]  # Top offset from baseline
        
        team_gap = 8 if team_logo_img else 0
        team_group_w = (20 if team_logo_img else 0) + team_gap + team_width
        team_group_x = cx + _center_x(cw, team_group_w)

        # Vertically center the logo on text baseline
        logo_x = team_group_x
        logo_y = y + (team_height // 2) - 10  # 20//2 = 10
        
        # Position text to the right of logo
        text_x = logo_x + 20 + team_gap
        
        # Position team text with baseline accounting
        text_y = y - team_offset

        if team_logo_img:
            card.paste(team_logo_img, (logo_x, logo_y), team_logo_img)
        card_draw.text((text_x, text_y), display_team, fill=_to_rgba(theme["text_muted"]), font=team_font)
        y += team_height + 18

        # Hero stat (featured stat) - full width box
        hero_value = goals  # All leagues focus on goals
        hero_label = "GOALS"
        
        hero_value_str = str(int(hero_value))
        hero_cell_h = 80  # Height for the hero cell
        
        _draw_stat_cell(
            card_draw,
            cx,
            y,
            cw,
            hero_cell_h,
            label=hero_label,
            value=hero_value_str,
            theme=theme,
            value_font=hero_value_font,
            label_font=hero_label_font,
        )
        y += hero_cell_h + 12

        # Seasons (optional)
        if seasons:
            seasons_text = " · ".join(sort_seasons(seasons))
            seasons_w, seasons_h = _measure_text(card_draw, seasons_text, seasons_font)
            card_draw.text((cx + (cw - seasons_w) // 2, y), seasons_text, fill=_to_rgba(theme["text_muted"]), font=seasons_font)
            y += seasons_h + 18
        else:
            y += 6

        # Compact stats (smaller pills, to leave room for awards/teams)
        featured_label = "POINTS"
        featured_value = points

        grid_gap = 10
        cell_w = (cw - (2 * grid_gap)) // 3
        cell_h = 48

        grid_rows = [
            [("GAMES", str(games)), ("ASSISTS", str(assists)), ("SAVES", str(saves))],
            [(featured_label, str(int(featured_value))), ("BLOCKS", str(blocks)), ("PP%", f"{pp_pct_val:.1f}")],
            [("OWN GOALS", str(own_goals)), ("PP PLAYS", str(pp_plays)), ("PP USED", str(pp_used))],
        ]

        for row in grid_rows:
            for i, (label, value) in enumerate(row):
                cell_x = cx + i * (cell_w + grid_gap)
                _draw_stat_cell(
                    card_draw,
                    cell_x,
                    y,
                    cell_w,
                    cell_h,
                    label=label,
                    value=value,
                    theme=theme,
                    value_font=stat_value_font,
                    label_font=stat_label_font,
                )
            y += cell_h + 8

        # Add spacing between stats and teams/awards section
        y += 12

        # Two-column layout: Teams (left) and Awards (right)
        column_gap = 20
        left_column_w = (cw - column_gap) // 2
        right_column_w = left_column_w
        left_x = cx
        right_x = cx + left_column_w + column_gap
        
        # Start both columns at the same Y position
        teams_y = y
        awards_y = y
        
        # Left column: Teams
        if teams_played or team_code:
            history: List[str] = [t for t in (teams_played or []) if t]
            if team_code:
                code_up = str(team_code).upper()
                history = [t for t in history if str(t).upper() != code_up]
                history.append(code_up)

            # Only show what fits in left column
            logo_size = 20
            spacing = 6
            max_fit = max((left_column_w + spacing) // (logo_size + spacing), 1)
            shown = history[-max_fit:]

            card_draw.text((left_x, teams_y), "TEAMS", fill=_to_rgba(theme["text_muted"]), font=section_font)
            teams_y += 20

            # Left-align teams starting from title position
            lx = left_x
            for team in shown:
                path = _find_logo_path(team)
                logo = _load_logo(path, logo_size) if path else None
                if logo:
                    card.paste(logo, (lx, teams_y), logo)
                else:
                    card_draw.ellipse((lx, teams_y, lx + logo_size, teams_y + logo_size), outline=_to_rgba(theme["row_divider"]), width=2)
                lx += logo_size + spacing
            teams_y += logo_size + 8
        
        # Right column: Awards
        awards_title = "AWARDS" if not awards else "AWARDS"
        card_draw.text((right_x, awards_y), awards_title, fill=_to_rgba(theme["text_muted"]), font=section_font)
        awards_y += 20

        if awards:

            counts: Dict[str, int] = {}
            for a in awards:
                code = _abbr_award(str(a))
                counts[code] = counts.get(code, 0) + 1

            # Sort by count desc then code
            items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            
            # Layout awards in right column with improved chip styling
            x = right_x
            line_h = 28
            max_lines = 3  # Allow more lines in narrower column
            line = 1
            for code, count in items:
                chip_w = _draw_award_chip(card_draw, x, awards_y, code=code, count=count, theme=theme, font=small_font)
                x += chip_w + 6  # Tighter spacing
                
                # Check if we need to wrap to next line
                if x + 60 > right_x + right_column_w and line < max_lines:  # 60px minimum for next chip
                    line += 1
                    x = right_x
                    awards_y += line_h + 6
                elif x + 60 > right_x + right_column_w:
                    break  # No more space
            
            awards_y += line_h + 8
        
        # Move Y past the taller of the two columns
        y = max(teams_y, awards_y)

        # Paste card onto canvas
        canvas.paste(card, (CANVAS_PADDING, CANVAS_PADDING), card)

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        image_bytes = buffer.getvalue()

        player_card_cache.set(cache_key, image_bytes)
        return _bytes_to_buffer(image_bytes)

    except Exception as e:
        logger.error("Error generating player card image: %s", e, exc_info=True)
        return None


async def generate_player_card(
    *,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_type: Optional[str] = None,
    season_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    seasons: Optional[List[str]] = None,
    awards: Optional[List[str]] = None,
    teams_played: Optional[List[str]] = None,
) -> Optional[bytes]:
    """Generate a player card PNG as bytes.

    Matches the signature used by cogs.cards.player_card. avatar_url is currently
    unused in this design.
    """
    try:
        buffer = generate_player_card_image(
            player_id=_normalize_key(player_name),
            player_name=player_name,
            team_name=team_name,
            team_code=team_code,
            league_code=league_type,
            season_name=season_name,
            stats=stats or {},
            seasons=seasons,
            awards=awards,
            teams_played=teams_played,
        )
        if buffer is None:
            return None
        return buffer.getvalue()
    except Exception as e:
        logger.error("Error in generate_player_card wrapper: %s", e, exc_info=True)
        return None

# NOTE: Legacy generator code was previously appended to this file during refactors.
# It is intentionally disabled to avoid overriding the standings-style renderer.
LEGACY_DISABLED = r'''
Player card image generator - shows player stats with v5 layout and league theming.

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
DATA_DIR = ROOT_DIR / "data"

FONTS_DIR = ROOT_DIR / "fonts"

# Theme configuration (reused from standings/match table)
BASE_THEME = {
    "canvas_gradient": ((6, 10, 22), (27, 8, 35)),
    "card_bg": (26, 33, 49, 255),
    "card_border": (58, 74, 108, 255),
    "header_gradient": ((90, 134, 255), (176, 92, 255)),
    "text_primary": (245, 248, 255, 255),
    "text_muted": (173, 187, 212, 255),
    "accent_color": (90, 134, 255, 255),
}

LEAGUE_THEME_OVERRIDES = {
    "NBL": {
        "canvas_gradient": ((45, 30, 18), (95, 60, 28)),
        "card_bg": (52, 35, 20, 255),
        "card_border": (111, 74, 38, 255),
        "header_gradient": ((192, 142, 86), (126, 74, 33)),
        "text_primary": (255, 250, 240, 255),
        "text_muted": (236, 210, 160, 255),
        "accent_color": (247, 173, 68, 255),
    },
    "MBL": {
        "canvas_gradient": ((24, 12, 2), (68, 38, 6)),
        "header_gradient": ((255, 150, 2), (191, 105, 2)),
        "card_bg": (48, 30, 8, 255),
        "card_border": (128, 78, 12, 255),
        "accent_color": (255, 149, 0, 255),
    },
    "NCBA": {
        "canvas_gradient": ((5, 24, 24), (9, 58, 70)),
        "header_gradient": ((0, 173, 181), (0, 119, 182)),
        "card_bg": (14, 34, 44, 255),
        "card_border": (22, 64, 78, 255),
        "accent_color": (126, 199, 255, 255),
    },
}

# Tier definitions (from v5)
TIERS = {
    'ADEX': {
        'min_goals': 999999, 'min_ppg': 999, 'min_games': 999999, 'min_seasons': 999999, 'min_turns': 999999,
        'primary': '#F4B1A5', 'secondary': '#E8A090',
        'card_bg': '#1A1615', 'accent_bg': '#2A2220'
    },
    'GOAT': {
        'min_goals': 999999, 'min_ppg': 999, 'min_games': 999999, 'min_seasons': 999999, 'min_turns': 999999,
        'primary': '#CCD6DD', 'secondary': '#B0BEC5',
        'card_bg': '#1A1C1E', 'accent_bg': '#252830'
    },
    'LEGEND': {
        'min_goals': 100, 'min_ppg': 0.95, 'min_games': 100, 'min_seasons': 5, 'min_turns': 0,
        'primary': '#FFD700', 'secondary': '#FFA500',
        'card_bg': '#1A1714', 'accent_bg': '#2A2520'
    },
    'ELITE': {
        'min_goals': 50, 'min_ppg': 0.5, 'min_games': 60, 'min_seasons': 4, 'min_turns': 2000,
        'primary': '#A78BFA', 'secondary': '#818CF8',
        'card_bg': '#1A1720', 'accent_bg': '#252030'
    },
    'PRO': {
        'min_goals': 35, 'min_ppg': 0.4, 'min_games': 35, 'min_seasons': 3, 'min_turns': 1000,
        'primary': '#34D399', 'secondary': '#10B981',
        'card_bg': '#141A18', 'accent_bg': '#1F2A25'
    },
    'RISING': {
        'min_goals': 15, 'min_ppg': 0.3, 'min_games': 15, 'min_seasons': 2, 'min_turns': 0,
        'primary': '#38BDF8', 'secondary': '#0EA5E9',
        'card_bg': '#14181A', 'accent_bg': '#1F2528'
    },
    'ROOKIE': {
        'min_goals': 0, 'min_ppg': 0, 'min_games': 0, 'min_seasons': 0, 'min_turns': 0,
        'primary': '#9CA3AF', 'secondary': '#6B7280',
        'card_bg': '#171717', 'accent_bg': '#222222'
    }
}

TIER_ORDER = ['ADEX', 'GOAT', 'LEGEND', 'ELITE', 'PRO', 'RISING', 'ROOKIE']


def calculate_tier(player_data: Dict, goats_list: List[str] = None) -> str:
    display_name = player_data.get('display_name', '')
    
    # Special Adex tier
    if display_name.lower() == 'adex':
        return 'ADEX'
    
    # Check if player is in GOATS list (use provided list or global)
    goats = goats_list if goats_list is not None else []
    if display_name.lower() in [g.lower() for g in goats]:
        return 'GOAT'
    
    # Check for manually assigned tier
    if player_data.get('tier'):
        return player_data['tier'].upper()
    
    goals = player_data.get('goals', 0)
    ppg = player_data.get('ppg', 0)
    games = player_data.get('games_played', 0)
    seasons = len(player_data.get('seasons', []))
    turns = player_data.get('turns_played', 0)
    
    # Calculate tier based on stats
    calculated_tier = 'ROOKIE'
    for tier_name in TIER_ORDER:
        if tier_name == 'GOAT':
            continue  # GOAT is manual only
        tier = TIERS[tier_name]
        if (goals >= tier['min_goals'] and 
            ppg >= tier['min_ppg'] and 
            games >= tier['min_games'] and
            seasons >= tier['min_seasons'] and
            turns >= tier.get('min_turns', 0)):
            calculated_tier = tier_name
            break  # Take the highest they qualify for
    
    # Apply minimum tier based on seasons played (balancing)
    if seasons >= 7:
        min_tier = 'ELITE'
    elif seasons >= 5:
        min_tier = 'PRO'
    elif seasons >= 3:
        min_tier = 'RISING'
    else:
        min_tier = 'ROOKIE'
    
    # Get the tier order index
    tier_order = {tier: idx for idx, tier in enumerate(TIER_ORDER)}
    
    # If calculated tier is lower than minimum, bump it up
    if tier_order.get(calculated_tier, 999) > tier_order.get(min_tier, 999):
        calculated_tier = min_tier
    
    return calculated_tier


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def sort_seasons(seasons_list):
    """Sort seasons by numeric order, not alphabetically"""
    season_order = {'S1':1, 'S2': 2, 'S3': 3, 'S4': 4, 'S5': 5, 'S5 D2': 5.5, 'S6': 6,
                   'S7': 7, 'S8': 8, 'S9': 9, 'S10': 10, 'S11': 11, 'S12': 12}
    return sorted(seasons_list, key=lambda s: season_order.get(s, 999))

# Layout constants (from v5 style)
CARD_WIDTH = 340
CARD_HEIGHT = 500
CARD_PADDING = 24
CARD_CORNER_RADIUS = 24

# Fonts
NAME_FONT_SIZE = 32
SEASONS_FONT_SIZE = 14
STAT_BIG_FONT_SIZE = 36
STAT_MED_FONT_SIZE = 18
STAT_LABEL_FONT_SIZE = 12
SECTION_FONT_SIZE = 14
SMALL_FONT_SIZE = 10
AWARD_COUNT_FONT_SIZE = 12

PLAYER_CARD_CACHE_TTL_SECONDS = 120


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


@lru_cache(maxsize=1)
def _team_colors_index() -> Dict[str, Dict[str, Any]]:
    """Load team color palettes from data/team_colors.json if available."""
    path = DATA_DIR / "team_colors.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        out: Dict[str, Dict[str, Any]] = {}
        for code, palette in (data or {}).items():
            out[(code or "").upper()] = palette or {}
        return out
    except Exception:
        logger.warning("Failed to load team colors from %s", path)
        return {}


player_card_cache = TTLCache(ttl_seconds=PLAYER_CARD_CACHE_TTL_SECONDS, max_size=32)  # Reduced from 64 to 32 for memory optimization


def clear_player_card_cache() -> None:
    """Clear cached player card images."""
    player_card_cache.clear()


def _make_cache_key(
    player_id: str,
    player_name: str,
    team_code: Optional[str],
    league_code: Optional[str],
    stats: Dict[str, Any],
    tier: str,
) -> str:
    payload = {
        "player_id": player_id,
        "player_name": player_name,
        "team_code": (team_code or "").upper(),
        "league_code": (league_code or "").upper(),
        "stats": stats,
        "tier": tier,
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
        image.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - image.width) // 2, (size - image.height) // 2)
        canvas.paste(image, offset, image)
        return canvas
    except Exception:
        logger.warning("Failed to load logo %s", path)
        return None


def _draw_vertical_gradient(draw, bbox, start, end):
    x0, y0, x1, y1 = bbox
    height = y1 - y0
    for i in range(height):
        p = i / height
        r = int(start[0] + (end[0] - start[0]) * p)
        g = int(start[1] + (end[1] - start[1]) * p)
        b = int(start[2] + (end[2] - start[2]) * p)
        draw.rectangle((x0, y0 + i, x1, y0 + i + 1), fill=(r, g, b))


def _load_fonts():
    try:
        name_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), NAME_FONT_SIZE)
        seasons_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), SEASONS_FONT_SIZE)
        stat_big_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), STAT_BIG_FONT_SIZE)
        stat_med_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), STAT_MED_FONT_SIZE)
        stat_label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), STAT_LABEL_FONT_SIZE)
        section_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), SECTION_FONT_SIZE)
        small_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), SMALL_FONT_SIZE)
        award_count_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), AWARD_COUNT_FONT_SIZE)
    except Exception:
        name_font = ImageFont.load_default()
        seasons_font = ImageFont.load_default()
        stat_big_font = ImageFont.load_default()
        stat_med_font = ImageFont.load_default()
        stat_label_font = ImageFont.load_default()
        section_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
        award_count_font = ImageFont.load_default()
    return name_font, seasons_font, stat_big_font, stat_med_font, stat_label_font, section_font, small_font, award_count_font


def _draw_award_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    *,
    code: str,
    count: int,
    theme: Dict[str, Any],
    font: ImageFont.ImageFont,
    pad_x: int = 10,
    height: int = 28,
) -> int:
    label = f"x{count} {code}"
    tw, th = _measure_text(draw, label, font)
    w = tw + pad_x * 2
    draw.rounded_rectangle(
        (x, y, x + w, y + height),
        radius=12,
        fill=_to_rgba(theme["row_alt"]),
        outline=_to_rgba(theme["row_divider"]),
        width=1,
    )
    tx = x + (w - tw) // 2
    ty = y + (height - th) // 2
    draw.text((tx, ty), label, fill=_to_rgba(theme["text_primary"]), font=font)
    return w


def _abbr_award(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())
    if not cleaned:
        return "AWD"
    if len(cleaned) <= 5:
        return cleaned
    return cleaned[:4]


def _abbr_award(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (text or "").upper())
    if not cleaned:
        return "AWD"
    if len(cleaned) <= 5:
        return cleaned
    return cleaned[:4]


def _draw_rounded_badge(draw, pos, text, font, theme, min_width=0):
    x, y = pos
    pad = 12  # padding

    tw, th = draw.textbbox((0, 0), text, font=font)[2:]

    bw = max(tw + pad * 2, min_width)
    bh = 36  # height

    draw.rounded_rectangle(
        (x, y, x + bw, y + bh),
        radius=18,  # corner radius
        fill=_to_rgba(theme["card_bg"]),
        outline=_to_rgba(theme["card_border"]),
        width=2
    )

    tx = x + (bw - tw) // 2
    ty = y + (bh - th) // 2 - 1

    draw.text((tx, ty), text, fill=_to_rgba(theme["text_primary"]), font=font)











def generate_player_card_image(
    player_id: str,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_code: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    seasons: Optional[List[str]] = None,
    awards: Optional[List[str]] = None,
    teams_played: Optional[List[str]] = None,
) -> Optional[io.BytesIO]:
    """
    Generate a player card image with v5 layout.
    
    Args:
        player_id: Unique player identifier
        player_name: Player display name
        team_name: Team name (for display)
        team_code: Team code (for logo lookup)
        league_code: League code for theming (NBL, MBL, NCBA)
        stats: Dict with stats like: goals, assists, saves, own_goals, ppg, games_played, turns_played
        seasons: List of seasons played
        awards: List of awards
        teams_played: List of teams played for
    
    Returns:
        BytesIO buffer with PNG image, or None on error
    """
    try:
        if stats is None:
            stats = {}
        if seasons is None:
            seasons = []
        if awards is None:
            awards = []
        if teams_played is None:
            teams_played = []
        
        # Calculate tier
        player_data = {
            'display_name': player_name,
            'goals': stats.get('goals', 0),
            'assists': stats.get('assists', 0),
            'saves': stats.get('saves', 0),
            'own_goals': stats.get('own_goals', 0),
            'ppg': stats.get('ppg', 0),
            'games_played': stats.get('games_played', 0),
            'turns_played': stats.get('turns_played', 0),
            'seasons': seasons,
            'teams_played': teams_played,
            'awards': awards,
        }
        tier_name = calculate_tier(player_data)
        tier = TIERS[tier_name]
        primary_rgb = hex_to_rgb(tier['primary'])
        
        # Check cache
        cache_key = _make_cache_key(player_id, player_name, team_code, league_code, stats, tier_name)
        cached_image = player_card_cache.get(cache_key)
        if cached_image is not None:
            logger.debug("Player card cache hit for %s", player_name)
            return _bytes_to_buffer(cached_image)
        
        theme = _get_theme(league_code)
        name_font, seasons_font, stat_big_font, stat_med_font, stat_label_font, section_font, small_font, award_count_font = _load_fonts()
        
        # Create canvas with transparent background
        canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        
        # Draw card background with tier color
        card_bg = hex_to_rgb(tier['card_bg'])
        draw.rounded_rectangle(
            (0, 0, CARD_WIDTH, CARD_HEIGHT),
            radius=CARD_CORNER_RADIUS,
            fill=card_bg + (255,),
            outline=primary_rgb + (255,),
            width=2,
        )
        
        y = CARD_PADDING
        
        # === TIER BADGE (top center) ===
        tier_text = tier_name
        bbox = draw.textbbox((0, 0), tier_text, font=name_font)
        tw = bbox[2] - bbox[0]
        draw.text(((CARD_WIDTH - tw) // 2, y), tier_text, fill=tier['primary'], font=name_font)
        y += 25
        
        # === PLAYER NAME + ACTIVE TEAM LOGO ===
        # Determine display team and icon
        display_team = team_name or "Free Agent"
        team_icon = None
        
        if team_code:
            path = _find_logo_path(team_code)
            if path:
                team_icon = _load_logo(path, 28)
        
        # Calculate name width
        bbox = draw.textbbox((0, 0), player_name, font=name_font)
        name_w = bbox[2] - bbox[0]
        
        # Total width (name + gap + icon)
        logo_size = 28
        gap = 10 if team_icon else 0
        total_w = name_w + gap + (logo_size if team_icon else 0)
        start_x = (CARD_WIDTH - total_w) // 2
        
        # Draw name
        draw.text((start_x, y), player_name, fill='#FFFFFF', font=name_font)
        
        # Draw team logo/icon next to name
        if team_icon:
            icon_x = start_x + name_w + gap
            icon_y = y + 1
            canvas.paste(team_icon, (icon_x, icon_y), team_icon)
        
        y += 38
        
        # === SEASONS ===
        if seasons:
            seasons_text = ' · '.join(sort_seasons(seasons))
            bbox = draw.textbbox((0, 0), seasons_text, font=seasons_font)
            tw = bbox[2] - bbox[0]
            draw.text(((CARD_WIDTH - tw) // 2, y), seasons_text, fill=tier['primary'], font=seasons_font)
        y += 22
        
        # === FEATURED STAT (Goals) ===
        goals = int(stats.get('goals', 0))
        goals_str = str(goals)
        bbox = draw.textbbox((0, 0), goals_str, font=stat_big_font)
        tw = bbox[2] - bbox[0]
        draw.text(((CARD_WIDTH - tw) // 2, y), goals_str, fill='#FFFFFF', font=stat_big_font)
        y += 35
        
        bbox = draw.textbbox((0, 0), 'GOALS', font=stat_label_font)
        tw = bbox[2] - bbox[0]
        draw.text(((CARD_WIDTH - tw) // 2, y), 'GOALS', fill='#CCCCCC', font=stat_label_font)
        y += 22
        
        # === STATS ROW 1 ===
        name_lower = player_name.lower()
        stats_row1 = [
            ('ASSISTS', int(stats.get('assists', 0))),
            ('SAVES', int(stats.get('saves', 0))),
            ('OGs', '∞' if name_lower == 'ennette' else int(stats.get('own_goals', 0)))
        ]
        
        stat_w = 90
        total_w = 3 * stat_w
        start_x = (CARD_WIDTH - total_w) // 2
        
        for i, (label, value) in enumerate(stats_row1):
            sx = start_x + i * stat_w
            val_str = str(value)
            bbox = draw.textbbox((0, 0), val_str, font=stat_med_font)
            tw = bbox[2] - bbox[0]
            draw.text((sx + (stat_w - tw) // 2, y), val_str, fill='#FFFFFF', font=stat_med_font)
        y += 24
        
        for i, (label, value) in enumerate(stats_row1):
            sx = start_x + i * stat_w
            bbox = draw.textbbox((0, 0), label, font=stat_label_font)
            tw = bbox[2] - bbox[0]
            draw.text((sx + (stat_w - tw) // 2, y), label, fill='#CCCCCC', font=stat_label_font)
        y += 22
        
        # === STATS ROW 2 ===
        stats_row2 = [
            ('PPG', f"{stats.get('ppg', 0):.2f}"),
            ('GAMES', int(stats.get('games_played', 0))),
            ('TURNS', int(stats.get('turns_played', 0)))
        ]
        
        for i, (label, value) in enumerate(stats_row2):
            sx = start_x + i * stat_w
            val_str = str(value)
            bbox = draw.textbbox((0, 0), val_str, font=stat_med_font)
            tw = bbox[2] - bbox[0]
            draw.text((sx + (stat_w - tw) // 2, y), val_str, fill='#FFFFFF', font=stat_med_font)
        y += 24
        
        for i, (label, value) in enumerate(stats_row2):
            sx = start_x + i * stat_w
            bbox = draw.textbbox((0, 0), label, font=stat_label_font)
            tw = bbox[2] - bbox[0]
            draw.text((sx + (stat_w - tw) // 2, y), label, fill='#CCCCCC', font=stat_label_font)
        y += 21
        
        # === STATS DISCLAIMER ===
        disclaimer_text = "Stats up to S10"
        bbox = draw.textbbox((0, 0), disclaimer_text, font=small_font)
        tw = bbox[2] - bbox[0]
        draw.text(((CARD_WIDTH - tw) // 2, y), disclaimer_text, fill='#888888', font=small_font)
        y += 12
        
        # === DIVIDER ===
        draw.line([(30, y), (CARD_WIDTH - 30, y)], fill=tier['primary'] + '40', width=1)
        y += 10
        
        # Add spacing between stats and teams/awards section
        y += 12
        
        # Two-column layout: Teams (left) and Awards (right)
        column_gap = 20
        left_column_w = (CARD_WIDTH - 2 * 24 - column_gap) // 2  # Account for padding
        right_column_w = left_column_w
        left_x = 24
        right_x = left_x + left_column_w + column_gap
        
        # Start both columns at the same Y position
        teams_y = y
        awards_y = y
        
        # Left column: Teams
        if teams_played or team_code:
            history: List[str] = [t for t in (teams_played or []) if t]
            if team_code:
                code_up = str(team_code).upper()
                history = [t for t in history if str(t).upper() != code_up]
                history.append(code_up)

            # Only show what fits in left column
            logo_size = 20
            spacing = 6
            max_fit = max((left_column_w + spacing) // (logo_size + spacing), 1)
            shown = history[-max_fit:]

            draw.text((left_x, teams_y), 'TEAMS', fill='#CCCCCC', font=section_font)
            teams_y += 16

            # Left-align teams starting from title position
            lx = left_x
            for team in shown:
                logo = _load_logo(_find_logo_path(team), size=logo_size)
                if logo:
                    canvas.paste(logo, (lx, teams_y), logo)
                else:
                    draw.ellipse([lx, teams_y, lx + logo_size, teams_y + logo_size], 
                                fill=tier['accent_bg'], outline=tier['primary'] + '60')
                lx += logo_size + spacing
            teams_y += logo_size + 8
        
        # Right column: Awards
        awards_title = "NO AWARDS" if not awards else "AWARDS"
        draw.text((right_x, awards_y), awards_title, fill='#CCCCCC', font=section_font)
        awards_y += 16

        if awards:

            counts: Dict[str, int] = {}
            for a in awards:
                code = _abbr_award(str(a))
                counts[code] = counts.get(code, 0) + 1

            # Sort by count desc then code
            items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            
            # Layout awards in right column with improved chip styling
            x = right_x
            line_h = 28
            max_lines = 3  # Allow more lines in narrower column
            line = 1
            for code, count in items:
                chip_w = _draw_award_chip(draw, x, awards_y, code=code, count=count, theme=theme, font=small_font)
                x += chip_w + 6  # Tighter spacing
                
                # Check if we need to wrap to next line
                if x + 60 > right_x + right_column_w and line < max_lines:  # 60px minimum for next chip
                    line += 1
                    x = right_x
                    awards_y += line_h + 6
                elif x + 60 > right_x + right_column_w:
                    break  # No more space
            
            awards_y += line_h + 8
        
        # Move Y past the taller of the two columns
        y = max(teams_y, awards_y)
        
        # Save to buffer
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        image_bytes = buffer.getvalue()
        
        # Cache the result
        player_card_cache.set(cache_key, image_bytes)
        
        return _bytes_to_buffer(image_bytes)
    
    except Exception as e:
        logger.error("Error generating player card image: %s", e, exc_info=True)
        return None


# Convenience async wrapper to match cog expectations
async def generate_player_card(
    *,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_type: Optional[str] = None,
    avatar_url: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    seasons: Optional[List[str]] = None,
    awards: Optional[List[str]] = None,
    teams_played: Optional[List[str]] = None,
) -> Optional[bytes]:
    """Generate a player card PNG as bytes.

    Matches the signature used by cogs.cards.player_card and adapts keys.
    """
    try:
        normalized_stats = stats or {}

        buffer = generate_player_card_image(
            player_id=_normalize_key(player_name),
            player_name=player_name,
            team_name=team_name,
            team_code=team_code,
            league_code=league_type,
            stats=normalized_stats,
            seasons=seasons,
            awards=awards,
            teams_played=teams_played,
        )

        if buffer is None:
            return None

        return buffer.getvalue()
    except Exception as e:
        logger.error("Error in generate_player_card wrapper: %s", e, exc_info=True)
        return None

    '''
