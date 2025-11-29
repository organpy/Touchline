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

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from utils.caching import TTLCache

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT_DIR / "images"
FONTS_DIR = ROOT_DIR / "fonts"

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
        name_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 28)
        label_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), 14)
        value_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 16)
        small_font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Regular.ttf"), 12)
    except Exception:
        name_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
        value_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    return name_font, label_font, value_font, small_font


def _draw_rounded_badge(
    draw: ImageDraw.ImageDraw,
    position: Tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    theme: Dict[str, Any],
    min_width: int = 100,
) -> Tuple[int, int]:
    """Draw a rounded badge with text and return its dimensions."""
    x, y = position
    
    # Measure text
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    # Calculate badge dimensions
    badge_width = max(text_width + BADGE_PADDING * 2, min_width)
    badge_height = BADGE_HEIGHT
    
    # Draw badge background
    draw.rounded_rectangle(
        (x, y, x + badge_width, y + badge_height),
        radius=BADGE_CORNER_RADIUS,
        fill=_to_rgba(theme["badge_bg"]),
        outline=_to_rgba(theme["badge_border"]),
        width=2,
    )
    
    # Draw text centered
    text_x = x + (badge_width - text_width) // 2
    text_y = y + (badge_height - text_height) // 2 - 2  # Slight adjustment for visual centering
    draw.text((text_x, text_y), text, fill=_to_rgba(theme["text_primary"]), font=font)
    
    return badge_width, badge_height


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


def _draw_star(
    draw: ImageDraw.ImageDraw,
    position: Tuple[int, int],
    size: int,
    color: Tuple[int, int, int, int],
    half: bool = False,
    right_half: bool = False,
):
    """Draw a simple star shape."""
    import math
    
    x, y = position
    center_x = x + size // 2
    center_y = y + size // 2
    outer_radius = size // 2
    inner_radius = outer_radius * 0.4
    
    # Calculate star points
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5  # Start at top
        radius = outer_radius if i % 2 == 0 else inner_radius
        px = center_x + radius * math.cos(angle)
        py = center_y - radius * math.sin(angle)
        points.append((px, py))
    
    if half:
        # Draw only half the star
        if right_half:
            # Clip to right half
            clipped_points = [(max(center_x, px), py) for px, py in points]
        else:
            # Clip to left half
            clipped_points = [(min(center_x, px), py) for px, py in points]
        draw.polygon(clipped_points, fill=color)
    else:
        draw.polygon(points, fill=color)


def _draw_avatar_circle(
    canvas: Image.Image,
    position: Tuple[int, int],
    size: int,
    border_color: Tuple[int, int, int, int],
    border_width: int = 4,
):
    """Draw a circular avatar placeholder with border."""
    draw = ImageDraw.Draw(canvas)
    x, y = position
    
    # Draw outer circle (border)
    draw.ellipse(
        (x, y, x + size, y + size),
        fill=_to_rgba(border_color),
    )
    
    # Draw inner circle (background)
    inner_offset = border_width
    draw.ellipse(
        (x + inner_offset, y + inner_offset, x + size - inner_offset, y + size - inner_offset),
        fill=(255, 255, 255, 255),
    )


def calculate_star_rating(stats: Dict[str, Any]) -> float:
    """
    Calculate star rating (0.5 to 5.0) based on player stats.
    
    This is a simple formula that can be tuned:
    - Points per game (PPG) weighted heavily
    - Goals, assists, saves all contribute
    - Games played affects reliability
    
    Returns a rating from 0.5 to 5.0 in 0.5 increments.
    """
    ppg = stats.get('ppg', 0) or 0
    goals = stats.get('total_goals', 0) or 0
    assists = stats.get('total_assists', 0) or 0
    saves = stats.get('total_saves', 0) or 0
    games = stats.get('games_played', 0) or 0
    
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


def _draw_rating_bar(
    draw: ImageDraw.ImageDraw,
    position: Tuple[int, int],
    rating: float,
    theme: Dict[str, Any],
    *,
    segment_count: int = 5,
    segment_width: int = 42,
    segment_height: int = 18,
    gap: int = 8,
) -> Tuple[int, int]:
    """Draw a segmented rating bar with gradient fill and partial segment support.
    Returns (total_width, height)."""
    x, y = position
    # Clamp rating
    rating = max(0.0, min(segment_count, rating))
    full_segments = int(rating)
    partial = rating - full_segments

    total_width = segment_count * segment_width + (segment_count - 1) * gap
    # Glow backdrop
    glow_rect = (x - 6, y - 6, x + total_width + 6, y + segment_height + 6)
    draw.rounded_rectangle(glow_rect, radius=segment_height, fill=(0, 0, 0, 40))

    for i in range(segment_count):
        seg_x0 = x + i * (segment_width + gap)
        seg_x1 = seg_x0 + segment_width
        seg_y0 = y
        seg_y1 = y + segment_height
        # Base segment background
        draw.rounded_rectangle(
            (seg_x0, seg_y0, seg_x1, seg_y1),
            radius=segment_height // 2,
            fill=_to_rgba(theme["badge_bg"]),
            outline=_to_rgba(theme["badge_border"]),
            width=2,
        )
        fill_ratio = 0.0
        if i < full_segments:
            fill_ratio = 1.0
        elif i == full_segments and partial > 0:
            fill_ratio = partial
        if fill_ratio > 0:
            # Gradient fill within this segment proportional to fill_ratio
            inner_w = int((segment_width - 4) * fill_ratio)
            grad_x0 = seg_x0 + 2
            grad_x1 = grad_x0 + inner_w
            grad_y0 = seg_y0 + 2
            grad_y1 = seg_y1 - 2
            start_col = theme.get("accent_color", (255, 215, 0, 255))
            end_col = (
                int(start_col[0] * 0.7),
                int(start_col[1] * 0.7),
                int(start_col[2] * 0.9),
                start_col[3],
            )
            for px in range(grad_x0, grad_x1):
                ratio = (px - grad_x0) / max(inner_w, 1)
                r = int(start_col[0] * (1 - ratio) + end_col[0] * ratio)
                g = int(start_col[1] * (1 - ratio) + end_col[1] * ratio)
                b = int(start_col[2] * (1 - ratio) + end_col[2] * ratio)
                draw.line([(px, grad_y0), (px, grad_y1)], fill=(r, g, b, 255))
    return total_width, segment_height


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
        
        # Check cache
        cache_key = _make_cache_key(player_id, player_name, team_code, league_code, stats, star_rating)
        cached_image = player_card_cache.get(cache_key)
        if cached_image is not None:
            logger.debug("Player card cache hit for %s", player_name)
            return _bytes_to_buffer(cached_image)
        
        theme = _get_theme(league_code)
        name_font, label_font, value_font, small_font = _load_fonts()
        
        # Create base canvas
        canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # --- Drop Shadow Layer ---
        shadow = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (8, 8, CARD_WIDTH - 4, CARD_HEIGHT - 4),
            radius=CARD_CORNER_RADIUS + 2,
            fill=(0, 0, 0, 120),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(12))
        canvas.alpha_composite(shadow)

        # --- Card Gradient Background (multi-layer) ---
        base = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        base_draw = ImageDraw.Draw(base)
        # Primary vertical gradient
        _draw_vertical_gradient(
            base_draw,
            (0, 0, CARD_WIDTH, CARD_HEIGHT),
            theme["bg_gradient_top"],
            theme["bg_gradient_bottom"],
        )
        # Diagonal overlay gradient (top-left to bottom-right)
        overlay = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        for i in range(CARD_WIDTH):
            ratio = i / CARD_WIDTH
            alpha = int(70 * (1 - ratio))  # Fade out to right
            col = theme.get("accent_color", (255, 255, 255, 255))
            overlay_draw.line([(i, 0), (i, CARD_HEIGHT)], fill=(col[0], col[1], col[2], alpha))
        base.alpha_composite(overlay)
        # Radial highlight center
        radial = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
        radial_draw = ImageDraw.Draw(radial)
        cx, cy = CARD_WIDTH // 2, CARD_HEIGHT // 3
        max_r = int(CARD_WIDTH * 0.6)
        highlight = theme.get("text_primary", (255, 255, 255, 255))
        for r in range(max_r, 0, -4):
            alpha = int(50 * (r / max_r))
            radial_draw.ellipse(
                (cx - r, cy - r, cx + r, cy + r),
                fill=(highlight[0], highlight[1], highlight[2], alpha),
            )
        base.alpha_composite(radial)
        # Mask for rounded corners
        mask = Image.new("L", (CARD_WIDTH, CARD_HEIGHT), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, CARD_WIDTH, CARD_HEIGHT),
            radius=CARD_CORNER_RADIUS,
            fill=255,
        )
        canvas.paste(base, (0, 0), mask)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1),
            radius=CARD_CORNER_RADIUS,
            outline=_to_rgba(theme["badge_border"]),
            width=3,
        )
        
        # === TOP LEFT: League Logo ===
        logo_x = CARD_PADDING
        logo_y = CARD_PADDING
        league_logo_path = _find_logo_path(league_code)
        if league_logo_path:
            logo_img = _load_logo(league_logo_path, LOGO_SIZE)
            if logo_img:
                canvas.paste(logo_img, (logo_x, logo_y), logo_img)
        
        # (Top stats removed per redesign; moved to bottom row)
        
        # === CENTER: Player Avatar Circle (smaller) ===
        avatar_size = 160
        avatar_x = (CARD_WIDTH - avatar_size) // 2
        avatar_y = CARD_PADDING + 40
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
        
        # === Team row with logo ===
        team_row_y = name_y + 30
        logo_inline_size = 40
        logo_img_inline = None
        if team_code:
            team_logo_path = _find_logo_path(team_code, team_name)
            if team_logo_path:
                logo_img_inline = _load_logo(team_logo_path, logo_inline_size)
        if team_name:
            team_bbox = draw.textbbox((0, 0), team_name, font=value_font)
            team_text_w = team_bbox[2] - team_bbox[0]
            total_inline_w = team_text_w + (logo_inline_size + 10 if logo_img_inline else 0)
            start_x = (CARD_WIDTH - total_inline_w) // 2
            if logo_img_inline:
                canvas.paste(logo_img_inline, (start_x, team_row_y - logo_inline_size // 4), logo_img_inline)
                text_x = start_x + logo_inline_size + 10
            else:
                text_x = start_x
            draw.text((text_x, team_row_y), team_name, fill=_to_rgba(theme["text_secondary"]), font=value_font)

        # === Rating bar below team ===
        rating_y = team_row_y + 42
        bar_w, bar_h = _draw_rating_bar(
            draw,
            ((CARD_WIDTH - (5 * 42 + 4 * 8)) // 2, rating_y),
            star_rating,
            theme,
        )
        
        # === Middle performance badges row ===
        perf_row_y = rating_y + 50
        ppg = stats.get('ppg', 0) or 0
        games = stats.get('games_played', 0) or 0
        total_points = stats.get('total_points', 0) or 0
        perf_badges = [
            (f"PPG: {ppg:.1f}", 140),
            (f"Games: {games}", 140),
            (f"Points: {total_points}", 160),
        ]
        total_perf_width = sum(w for _, w in perf_badges) + (len(perf_badges) - 1) * 16
        start_x_perf = (CARD_WIDTH - total_perf_width) // 2
        current_x = start_x_perf
        max_h = 0
        for text, mw in perf_badges:
            bw, bh = _draw_rounded_badge(draw, (current_x, perf_row_y), text, label_font, theme, min_width=mw)
            current_x += bw + 16
            max_h = max(max_h, bh)
        
        # === Bottom stats row (Goals / Assists / Saves / OGs) ===
        stats_row_y = perf_row_y + max_h + 48
        core_stats = [
            ("Goals", stats.get("total_goals", 0)),
            ("Assists", stats.get("total_assists", 0)),
            ("Saves", stats.get("total_saves", 0)),
            ("OGs", stats.get("total_own_goals", 0)),
        ]
        badge_min_w = 120
        total_core_width = len(core_stats) * badge_min_w + (len(core_stats) - 1) * 14
        start_x_core = (CARD_WIDTH - total_core_width) // 2
        cx = start_x_core
        for label, value in core_stats:
            _draw_rounded_badge(draw, (cx, stats_row_y), f"{label}: {value}", label_font, theme, min_width=badge_min_w)
            cx += badge_min_w + 14
        
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
