"""Player card image generator - shows player stats with league theming and star ratings."""

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
NAME_FONT = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 32)
LABEL_FONT = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 16)
VALUE_FONT = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 18)

# Theme configuration per league (reuses patterns from standings/match table)
BASE_THEME = {
    "bg_gradient_top": (135, 206, 250),     # Light sky blue
    "bg_gradient_bottom": (100, 149, 237),  # Cornflower blue
    "card_bg": (26, 33, 49, 255),
    "badge_bg": (30, 40, 60, 255),
    "badge_border": (58, 74, 108, 255),
    "text_primary": (255, 255, 255, 255),
    "text_secondary": (200, 210, 220, 255),
    "accent_color": (90, 134, 255, 255),
    "star_filled": (255, 215, 0, 255),      # Gold
    "star_empty": (100, 100, 100, 255),     # Gray
}

LEAGUE_THEME_OVERRIDES = {
    "NBL": {
        "bg_gradient_top": (192, 142, 86),
        "bg_gradient_bottom": (95, 60, 28),
        "badge_bg": (52, 35, 20, 255),
        "badge_border": (111, 74, 38, 255),
        "text_primary": (255, 250, 240, 255),
        "text_secondary": (236, 210, 160, 255),
        "accent_color": (247, 173, 68, 255),
    },
    "MBL": {
        "bg_gradient_top": (255, 150, 2),
        "bg_gradient_bottom": (68, 38, 6),
        "badge_bg": (48, 30, 8, 255),
        "badge_border": (128, 78, 12, 255),
        "accent_color": (255, 149, 0, 255),
    },
    "NCBA": {
        "bg_gradient_top": (0, 173, 181),
        "bg_gradient_bottom": (9, 58, 70),
        "badge_bg": (14, 34, 44, 255),
        "badge_border": (22, 64, 78, 255),
        "accent_color": (126, 199, 255, 255),
    },
}

# Tier theming per division/league
TIER_ORDER = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]

TIER_THEMES = {
    "default": {
        "Bronze": {
            "accent_color": (205, 127, 50, 255),
            "star_filled": (205, 127, 50, 255),
            "badge_border": (130, 82, 30, 255),
        },
        "Silver": {
            "accent_color": (192, 192, 192, 255),
            "star_filled": (192, 192, 192, 255),
            "badge_border": (122, 122, 122, 255),
        },
        "Gold": {
            "accent_color": (255, 215, 0, 255),
            "star_filled": (255, 215, 0, 255),
            "badge_border": (180, 150, 0, 255),
        },
        "Platinum": {
            "accent_color": (229, 228, 226, 255),
            "star_filled": (229, 228, 226, 255),
            "badge_border": (150, 150, 148, 255),
        },
        "Diamond": {
            "accent_color": (185, 242, 255, 255),
            "star_filled": (126, 199, 255, 255),
            "badge_border": (80, 170, 220, 255),
        },
    },
    "NBL": {
        "Gold": {"accent_color": (247, 173, 68, 255)},
        "Diamond": {"accent_color": (255, 240, 200, 255)},
    },
    "MBL": {
        "Gold": {"accent_color": (255, 149, 0, 255)},
        "Diamond": {"accent_color": (255, 210, 160, 255)},
    },
    "NCBA": {
        "Diamond": {"accent_color": (126, 199, 255, 255)},
    },
}

# Layout constants
CARD_WIDTH = 500
CARD_HEIGHT = 750
CARD_PADDING = 24
CARD_CORNER_RADIUS = 24
AVATAR_SIZE = 200
AVATAR_BORDER_WIDTH = 4
LOGO_SIZE = 64
BADGE_HEIGHT = 36
BADGE_CORNER_RADIUS = 18
BADGE_PADDING = 12

# Star rating constants
STAR_SIZE = 20
STAR_SPACING = 4
MAX_STARS = 5

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


def _rating_to_tier(rating: float, league_code: Optional[str]) -> str:
    """Map star rating to tier label; can be tuned per league if needed."""
    # Default mapping
    if rating < 1.5:
        return "Bronze"
    if rating < 2.5:
        return "Silver"
    if rating < 3.5:
        return "Gold"
    if rating < 4.5:
        return "Platinum"
    return "Diamond"


def _apply_tier_theme(base: Dict[str, Any], league_code: Optional[str], tier: str) -> Dict[str, Any]:
    """Return theme merged with tier-specific overrides (league-aware)."""
    theme = {**base}
    tier_overrides = TIER_THEMES.get("default", {}).get(tier, {})
    theme.update(tier_overrides)
    if league_code:
        per_league = TIER_THEMES.get(league_code.upper(), {}).get(tier, {})
        theme.update(per_league)
    return theme


def _apply_team_accent(theme: Dict[str, Any], team_code: Optional[str]) -> Dict[str, Any]:
    """Blend team accent colors into theme for richer visuals."""
    if not team_code:
        return theme
    palettes = _team_colors_index()
    pal = palettes.get((team_code or "").upper())
    if not pal:
        return theme
    accent = pal.get("primary") or pal.get("accent")
    border = pal.get("secondary") or pal.get("dark")
    new_theme = {**theme}
    if isinstance(accent, list) and len(accent) >= 3:
        new_theme["accent_color"] = _to_rgba(tuple(accent[:3]))
    if isinstance(border, list) and len(border) >= 3:
        new_theme["badge_border"] = _to_rgba(tuple(border[:3]))
    return new_theme


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


player_card_cache = TTLCache(ttl_seconds=PLAYER_CARD_CACHE_TTL_SECONDS, max_size=64)


def clear_player_card_cache() -> None:
    """Clear cached player card images."""
    player_card_cache.clear()


def _make_cache_key(
    player_id: str,
    player_name: str,
    team_code: Optional[str],
    league_code: Optional[str],
    stats: Dict[str, Any],
    star_rating: float,
) -> str:
    payload = {
        "player_id": player_id,
        "player_name": player_name,
        "team_code": (team_code or "").upper(),
        "league_code": (league_code or "").upper(),
        "stats": stats,
        "star_rating": star_rating,
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


def _load_fonts() -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Load and return the required fonts."""
    name_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 32)
    label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 16)
    value_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 18)
    small_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 14)
    return name_font, label_font, value_font, small_font


def _draw_rounded_badge(draw, pos, text, font, theme, min_width=0):
    x, y = pos
    pad = BADGE_PADDING

    tw, th = draw.textbbox((0, 0), text, font=font)[2:]

    bw = max(tw + pad * 2, min_width)
    bh = BADGE_HEIGHT

    draw.rounded_rectangle(
        (x, y, x + bw, y + bh),
        radius=BADGE_CORNER_RADIUS,
        fill=_to_rgba(theme["badge_bg"]),
        outline=_to_rgba(theme["badge_border"]),
        width=2
    )

    tx = x + (bw - tw) // 2
    ty = y + (bh - th) // 2 - 1

    draw.text((tx, ty), text, fill=_to_rgba(theme["text_primary"]), font=font)


def _draw_star_rating(
    draw: ImageDraw.ImageDraw,
    position: Tuple[int, int],
    rating: float,
    theme: Dict[str, Any],
) -> int:
    """Draw star rating (0.5 to 5.0) and return total width."""
    x, y = position
    
    # Clamp rating to valid range
    rating = max(0, min(5, rating))
    full_stars = int(rating)
    half_star = rating - full_stars >= 0.5
    
    total_width = 0
    for i in range(MAX_STARS):
        star_x = x + i * (STAR_SIZE + STAR_SPACING)
        
        if i < full_stars:
            # Full star
            _draw_star(draw, (star_x, y), STAR_SIZE, _to_rgba(theme["star_filled"]))
        elif i == full_stars and half_star:
            # Half star
            _draw_star(draw, (star_x, y), STAR_SIZE, _to_rgba(theme["star_filled"]), half=True)
            _draw_star(draw, (star_x, y), STAR_SIZE, _to_rgba(theme["star_empty"]), half=True, right_half=True)
        else:
            # Empty star
            _draw_star(draw, (star_x, y), STAR_SIZE, _to_rgba(theme["star_empty"]))
        
        total_width = star_x + STAR_SIZE - x
    
    return total_width


def _draw_star(draw, position, size, color, half=False, right_half=False):
    import math

    x, y = position
    cx = x + size / 2
    cy = y + size / 2
    r_outer = size * 0.48
    r_inner = r_outer * 0.45

    pts = []
    for i in range(10):
        angle = math.pi / 2 + i * (math.pi / 5)
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))

    if half:
        midpoint = cx
        if right_half:
            pts = [(max(px, midpoint), py) for px, py in pts]
        else:
            pts = [(min(px, midpoint), py) for px, py in pts]

    draw.polygon(pts, fill=color)


def _draw_avatar_circle(canvas, position, size, border_color, border_width=4):
    draw = ImageDraw.Draw(canvas)
    x, y = position

    # Shadow
    shadow = Image.new("RGBA", (size + 20, size + 20), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((10, 10, size + 10, size + 10), fill=(0, 0, 0, 90))
    canvas.paste(shadow, (x - 10, y - 10), shadow)

    # Border
    draw.ellipse((x, y, x + size, y + size), fill=_to_rgba(border_color))

    # Inner circle
    inner = border_width
    draw.ellipse(
        (x + inner, y + inner, x + size - inner, y + size - inner),
        fill=(255, 255, 255, 255),
    )


def _draw_holo_overlay(canvas: Image.Image, intensity: float = 0.15) -> None:
    """Add a subtle holographic diagonal stripe overlay for high tiers."""
    w, h = canvas.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    step = 16
    alpha = int(255 * intensity)
    for i in range(-h, w, step):
        draw.line((i, 0, i + h, h), fill=(255, 255, 255, alpha // 6), width=2)
        draw.line((i + 4, 0, i + h + 4, h), fill=(126, 199, 255, alpha // 6), width=1)
        draw.line((i + 8, 0, i + h + 8, h), fill=(255, 215, 0, alpha // 6), width=1)
    canvas.alpha_composite(overlay)


def _draw_vignette(canvas: Image.Image, intensity: float = 0.18) -> None:
    """Darken edges for depth using a radial vignette."""
    w, h = canvas.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    steps = 16
    for i in range(steps):
        alpha = int(255 * intensity * (i / steps))
        pad = int(min(w, h) * (i / steps) * 0.06)
        draw.rounded_rectangle(
            (0 + pad, 0 + pad, w - pad, h - pad),
            radius=max(8, int(24 - i)),
            outline=(0, 0, 0, alpha),
            width=2,
        )
    canvas.alpha_composite(overlay)


def _draw_watermark_logo(canvas: Image.Image, team_code: Optional[str], size: int = 280, alpha: int = 24) -> None:
    """Large faint team logo centered as watermark."""
    if not team_code:
        return
    path = _find_logo_path(team_code)
    if not path:
        return
    logo = _load_logo(path, size)
    if not logo:
        return
    wm = logo.copy()
    wm.putalpha(alpha)
    x = (CARD_WIDTH - size) // 2
    y = CARD_PADDING - 10
    canvas.paste(wm, (x, y), wm)


def _draw_stat_bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, value: float, max_value: float, label: str, theme: Dict[str, Any]) -> None:
    """Draw a horizontal stat bar with label and value."""
    height = 10
    radius = 6
    bg = (255, 255, 255, 40)
    fg = _to_rgba(theme.get("accent_color", (255, 215, 0, 255)))
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=bg)
    pct = 0 if max_value <= 0 else max(0.0, min(1.0, float(value) / float(max_value)))
    val_w = int(width * pct)
    if val_w >= 2:
        draw.rounded_rectangle((x, y, x + val_w, y + height), radius=radius, fill=fg)
    text = f"{label}: {value:.1f}" if isinstance(value, (int, float)) else f"{label}: {value}"
    draw.text((x, y - 14), text, fill=(255, 255, 255, 200), font=LABEL_FONT)


def calculate_star_rating(stats: Dict[str, Any]) -> float:
    """
    Calculate star rating (0.5 to 5.0) based on player stats.
    
    This is a simple formula that can be tuned:
    - Points per game (PPG) weighted heavily
    - Goals, assists, saves all contribute
    - Games played affects reliability
    
    Returns a rating from 0.5 to 5.0 in 0.5 increments.
    """
    # Support both normalized and alternative stats keys
    ppg = stats.get('ppg', 0) or 0
    goals = stats.get('total_goals', stats.get('goals', 0) or 0) or 0
    assists = stats.get('total_assists', stats.get('assists', 0) or 0) or 0
    saves = stats.get('total_saves', stats.get('saves', 0) or 0) or 0
    games = stats.get('games_played', stats.get('games', 0) or 0) or 0
    
    if games == 0:
        return 0.5
    
    # Calculate composite score
    # PPG is the main factor (typical range: 0-30)
    # Goals, assists, saves add to the score
    avg_goals = goals / games
    avg_assists = assists / games
    avg_saves = saves / games
    
    # Score formula (adjust weights as needed)
    score = (ppg * 0.5) + (avg_goals * 3) + (avg_assists * 2) + (avg_saves * 1)
    
    # Map score to stars (0-20 score range → 0.5-5.0 stars)
    # Score 0 → 0.5 stars, Score 20+ → 5.0 stars
    raw_rating = min(5.0, max(0.5, score / 4))
    
    # Round to nearest 0.5
    return round(raw_rating * 2) / 2


def generate_player_card_image(
    player_id: str,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_code: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    star_rating: Optional[float] = None,
) -> Optional[io.BytesIO]:
    """
    Generate a player card image with stats and star rating.
    
    Args:
        player_id: Unique player identifier
        player_name: Player display name
        team_name: Team name (for display)
        team_code: Team code (for logo lookup)
        league_code: League code for theming (NBL, MBL, NCBA)
        stats: Dict with stats like: goals, assists, saves, own_goals, points, ppg, games_played
        star_rating: Override star rating (0.5-5.0), or None to auto-calculate
    
    Returns:
        BytesIO buffer with PNG image, or None on error
    """
    try:
        if stats is None:
            stats = {}
        
        # Calculate star rating if not provided
        if star_rating is None:
            star_rating = calculate_star_rating(stats)
        tier_label = _rating_to_tier(star_rating, league_code)
        
        # Check cache
        cache_key = _make_cache_key(player_id, player_name, team_code, league_code, stats, star_rating) + f"|{tier_label}"
        cached_image = player_card_cache.get(cache_key)
        if cached_image is not None:
            logger.debug("Player card cache hit for %s", player_name)
            return _bytes_to_buffer(cached_image)
        
        theme = _get_theme(league_code)
        theme = _apply_tier_theme(theme, league_code, tier_label)
        name_font, label_font, value_font, small_font = _load_fonts()
        
        # Create canvas with gradient background
        canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        
        # Draw rounded card background with gradient
        # First draw the rounded rect as a mask
        mask = Image.new("L", (CARD_WIDTH, CARD_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, CARD_WIDTH, CARD_HEIGHT),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        
        # Create gradient image
        gradient = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        _draw_vertical_gradient(
            gradient_draw,
            (0, 0, CARD_WIDTH, CARD_HEIGHT),
            theme["bg_gradient_top"],
            theme["bg_gradient_bottom"],
        )
        
        # Apply mask to gradient
        canvas.paste(gradient, (0, 0), mask)
        draw = ImageDraw.Draw(canvas)

        # Watermark team logo for depth
        _draw_watermark_logo(canvas, team_code, size=280, alpha=24)
        
        # Draw card border
        draw.rounded_rectangle(
            (0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1),
            radius=CARD_CORNER_RADIUS,
            outline=_to_rgba(theme["badge_border"]),
            width=3,
        )

        # Tier badge (top-right)
        tier_badge_min_width = 120
        _draw_rounded_badge(
            draw,
            (CARD_WIDTH - CARD_PADDING - tier_badge_min_width, CARD_PADDING),
            tier_label.upper(),
            LABEL_FONT,
            theme,
            min_width=tier_badge_min_width,
        )
        
        # === TOP LEFT: League Logo ===
        logo_x = CARD_PADDING
        logo_y = CARD_PADDING
        league_logo_path = _find_logo_path(league_code)
        if league_logo_path:
            logo_img = _load_logo(league_logo_path, LOGO_SIZE)
            if logo_img:
                canvas.paste(logo_img, (logo_x, logo_y), logo_img)
        
        # === CENTER: Player Avatar Circle ===
        avatar_size = 160
        avatar_x = (CARD_WIDTH - avatar_size) // 2
        avatar_y = CARD_PADDING + 20
        _draw_avatar_circle(
            canvas,
            (avatar_x, avatar_y),
            avatar_size,
            _to_rgba(theme["badge_border"]),
            AVATAR_BORDER_WIDTH,
        )
        
        # === CENTER BELOW AVATAR: Player Name ===
        name_y = avatar_y + avatar_size + 16
        name_bbox = draw.textbbox((0, 0), player_name, font=name_font)
        name_width = name_bbox[2] - name_bbox[0]
        name_x = (CARD_WIDTH - name_width) // 2
        draw.text((name_x, name_y), player_name, fill=_to_rgba(theme["text_primary"]), font=name_font)

        # Accent underline under name using team accent
        theme = _apply_team_accent(theme, team_code)
        underline_w = name_width
        underline_x = name_x
        underline_y = name_y + (name_bbox[3] - name_bbox[1]) + 6
        draw.rounded_rectangle(
            (underline_x, underline_y, underline_x + underline_w, underline_y + 4),
            radius=2,
            fill=_to_rgba(theme["accent_color"]),
        )
        
        # --- BELOW NAME: Team Logo + Team Name (centered row) ---
        team_row_y = name_y + 34

        team_logo_size = 40
        team_logo_img = None

        if team_code:
            path = _find_logo_path(team_code)
            if path:
                team_logo_img = _load_logo(path, team_logo_size)

        # Measure team text
        team_text = team_name or ""
        team_text_bbox = draw.textbbox((0, 0), team_text, font=value_font)
        team_text_w = team_text_bbox[2] - team_text_bbox[0]

        # Total width incl. logo and gap
        gap = 10
        row_width = team_text_w + (team_logo_size + gap if team_logo_img else 0)

        start_x = (CARD_WIDTH - row_width) // 2

        # Draw logo
        if team_logo_img:
            canvas.paste(team_logo_img, (start_x, team_row_y), team_logo_img)
            text_x = start_x + team_logo_size + gap
        else:
            text_x = start_x

        draw.text((text_x, team_row_y + 6), team_text, fill=_to_rgba(theme["text_secondary"]), font=value_font)

        # === Star Rating below team ===
        star_y = team_row_y + 36
        star_total_width = MAX_STARS * STAR_SIZE + (MAX_STARS - 1) * STAR_SPACING
        star_x = (CARD_WIDTH - star_total_width) // 2
        _draw_star_rating(draw, (star_x, star_y), star_rating, theme)
        # Numeric rating label below stars
        rating_label = f"{star_rating:.1f}/5.0"
        r_bbox = draw.textbbox((0, 0), rating_label, font=small_font)
        r_w = r_bbox[2] - r_bbox[0]
        draw.text(((CARD_WIDTH - r_w) // 2, star_y + STAR_SIZE + 6), rating_label, fill=_to_rgba(theme["text_secondary"]), font=small_font)
        # === BOTTOM SECTION: Stats Grid (3x2 layout) ===
        stats_section_y = star_y + 40
        grid_gap = 14
        row_height = BADGE_HEIGHT + grid_gap
        col_width = (CARD_WIDTH - CARD_PADDING * 2) // 2

        left_col = CARD_PADDING
        right_col = CARD_PADDING + col_width + 8
        
        ppg = stats.get('ppg', 0) or 0
        games = stats.get('games_played', stats.get('games', 0) or 0) or 0
        total_points = stats.get('total_points', 0) or 0
        # Accept averages by multiplying with games if totals missing
        total_goals = stats.get('total_goals', 0) or (
            (stats.get('avg_goals', 0) or 0) * games
        )
        total_assists = stats.get('total_assists', 0) or (
            (stats.get('avg_assists', 0) or 0) * games
        )
        total_saves = stats.get('total_saves', 0) or (
            (stats.get('avg_saves', 0) or 0) * games
        )
        
        # Row 1
        _draw_rounded_badge(
            draw, (left_col, stats_section_y), f"PPG: {ppg:.1f}", label_font, theme, min_width=col_width - 16
        )
        _draw_rounded_badge(
            draw, (right_col, stats_section_y), f"Games: {games}", label_font, theme, min_width=col_width - 16
        )
        
        # Row 2
        _draw_rounded_badge(
            draw, (left_col, stats_section_y + row_height + grid_gap), f"Goals: {total_goals}", label_font, theme, min_width=col_width - 16
        )
        _draw_rounded_badge(
            draw, (right_col, stats_section_y + row_height + grid_gap), f"Assists: {total_assists}", label_font, theme, min_width=col_width - 16
        )
        
        # Row 3
        _draw_rounded_badge(
            draw, (left_col, stats_section_y + (row_height + grid_gap) * 2), f"Saves: {total_saves}", label_font, theme, min_width=col_width - 16
        )
        _draw_rounded_badge(
            draw, (right_col, stats_section_y + (row_height + grid_gap) * 2), f"Points: {total_points}", label_font, theme, min_width=col_width - 16
        )
        
        # Stat bars for richer visuals
        bars_y = stats_section_y + (row_height + grid_gap) * 3 + 18
        bars_x = CARD_PADDING
        bars_w = CARD_WIDTH - CARD_PADDING * 2
        # Heuristic maxima
        _draw_stat_bar(draw, bars_x, bars_y, bars_w, ppg, 30, "PPG", theme)
        _draw_stat_bar(draw, bars_x, bars_y + 26, bars_w, total_goals, max(1, games * 3), "Goals", theme)
        _draw_stat_bar(draw, bars_x, bars_y + 52, bars_w, total_assists, max(1, games * 3), "Assists", theme)
        _draw_stat_bar(draw, bars_x, bars_y + 78, bars_w, total_saves, max(1, games * 3), "Saves", theme)
        _draw_stat_bar(draw, bars_x, bars_y + 104, bars_w, total_points, max(1, games * 30), "Points", theme)
        _draw_stat_bar(draw, bars_x, bars_y + 130, bars_w, games, 20, "Games", theme)

        # Vignette for depth
        _draw_vignette(canvas, intensity=0.18)

        # Holographic overlay for top tiers
        if tier_label in ("Platinum", "Diamond"):
            _draw_holo_overlay(canvas, intensity=0.12 if tier_label == "Platinum" else 0.18)

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
) -> Optional[bytes]:
    """Generate a player card PNG as bytes.

    Matches the signature used by cogs.cards.player_card and adapts keys.
    Currently avatar images are not embedded; the circular avatar is stylistic.
    """
    try:
        normalized_stats = stats or {}

        # Ensure normalized keys for generator and rating
        games = normalized_stats.get('games_played', normalized_stats.get('games', 0) or 0) or 0
        if 'games_played' not in normalized_stats and 'games' in normalized_stats:
            normalized_stats['games_played'] = games

        # Convert averages to totals if totals missing
        for key in ('goals', 'assists', 'saves'):
            total_key = f'total_{key}'
            avg_key = f'avg_{key}'
            if total_key not in normalized_stats:
                normalized_stats[total_key] = (normalized_stats.get(avg_key, 0) or 0) * games

        # Compute star rating from normalized stats
        rating = calculate_star_rating(normalized_stats)

        buffer = generate_player_card_image(
            player_id=_normalize_key(player_name),
            player_name=player_name,
            team_name=team_name,
            team_code=team_code,
            league_code=league_type,
            stats=normalized_stats,
            star_rating=rating,
        )

        if buffer is None:
            return None

        return buffer.getvalue()
    except Exception as e:
        logger.error("Error in generate_player_card wrapper: %s", e, exc_info=True)
        return None
