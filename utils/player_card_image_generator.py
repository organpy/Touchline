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
SECTION_FONT_SIZE = 13
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


player_card_cache = TTLCache(ttl_seconds=PLAYER_CARD_CACHE_TTL_SECONDS, max_size=32) 


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
        title_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 24) 
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
        line_gap = 16  # Increased gap to prevent overlap
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


