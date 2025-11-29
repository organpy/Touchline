"""
Player Card Image Generator
Generates stylized player cards with league-specific theming.
"""

import io
import logging
from typing import Optional, Dict, Any, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from cachetools import TTLCache
import aiohttp

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Card dimensions
CARD_WIDTH = 400
CARD_HEIGHT = 520

# Cache for generated cards (5 minute TTL, max 100 cards)
_card_cache: TTLCache = TTLCache(maxsize=100, ttl=300)

# League-specific theme overrides
LEAGUE_THEME_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "NBL": {
        "gradient_top": (192, 142, 86),      # Gold
        "gradient_bottom": (95, 60, 28),      # Dark brown
        "accent": (255, 215, 120),            # Light gold
        "text_primary": (255, 255, 255),
        "text_secondary": (230, 210, 180),
        "badge_bg": (40, 30, 20, 200),
        "star_color": (255, 215, 0),          # Gold stars
    },
    "MBL": {
        "gradient_top": (255, 150, 2),        # Bright orange
        "gradient_bottom": (68, 38, 6),       # Dark brown
        "accent": (255, 200, 100),            # Light orange
        "text_primary": (255, 255, 255),
        "text_secondary": (255, 220, 180),
        "badge_bg": (50, 30, 10, 200),
        "star_color": (255, 180, 50),         # Orange stars
    },
    "NCBA": {
        "gradient_top": (0, 173, 181),        # Teal
        "gradient_bottom": (9, 58, 70),       # Dark teal
        "accent": (100, 220, 230),            # Light teal
        "text_primary": (255, 255, 255),
        "text_secondary": (200, 240, 245),
        "badge_bg": (10, 40, 50, 200),
        "star_color": (100, 220, 230),        # Teal stars
    },
}

# Default theme (fallback)
DEFAULT_THEME: Dict[str, Any] = {
    "gradient_top": (60, 60, 80),
    "gradient_bottom": (20, 20, 35),
    "accent": (120, 120, 180),
    "text_primary": (255, 255, 255),
    "text_secondary": (180, 180, 200),
    "badge_bg": (30, 30, 50, 200),
    "star_color": (255, 215, 0),
}


# ─────────────────────────────────────────────────────────────────────────────
# FONT LOADING
# ─────────────────────────────────────────────────────────────────────────────

_fonts_loaded = False
_font_bold_large: Optional[ImageFont.FreeTypeFont] = None
_font_bold_medium: Optional[ImageFont.FreeTypeFont] = None
_font_regular: Optional[ImageFont.FreeTypeFont] = None
_font_small: Optional[ImageFont.FreeTypeFont] = None


def _load_fonts() -> None:
    """Load fonts with fallbacks."""
    global _fonts_loaded, _font_bold_large, _font_bold_medium, _font_regular, _font_small
    
    if _fonts_loaded:
        return
    
    font_paths = [
        "fonts/Montserrat-Bold.ttf",
        "fonts/Roboto-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    
    regular_paths = [
        "fonts/Montserrat-Regular.ttf",
        "fonts/Roboto-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    
    # Load bold font
    for path in font_paths:
        try:
            _font_bold_large = ImageFont.truetype(path, 36)
            _font_bold_medium = ImageFont.truetype(path, 24)
            break
        except (OSError, IOError):
            continue
    
    # Load regular font
    for path in regular_paths:
        try:
            _font_regular = ImageFont.truetype(path, 20)
            _font_small = ImageFont.truetype(path, 16)
            break
        except (OSError, IOError):
            continue
    
    # Fallback to default
    if _font_bold_large is None:
        _font_bold_large = ImageFont.load_default()
        _font_bold_medium = ImageFont.load_default()
    if _font_regular is None:
        _font_regular = ImageFont.load_default()
        _font_small = ImageFont.load_default()
    
    _fonts_loaded = True


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _get_theme(league_type: str) -> Dict[str, Any]:
    """Get theme for a league type."""
    return LEAGUE_THEME_OVERRIDES.get(league_type.upper(), DEFAULT_THEME)


def _calculate_star_rating(stats: Dict[str, Any]) -> float:
    """
    Calculate star rating (0-5) based on player stats.
    Formula: (ppg * 0.5) + (avg_goals * 3) + (avg_assists * 2) + (avg_saves * 1)
    """
    ppg = stats.get("ppg", 0) or 0
    avg_goals = stats.get("avg_goals", 0) or 0
    avg_assists = stats.get("avg_assists", 0) or 0
    avg_saves = stats.get("avg_saves", 0) or 0
    
    raw_score = (ppg * 0.5) + (avg_goals * 3) + (avg_assists * 2) + (avg_saves * 1)
    
    # Normalize to 0-5 stars (assuming max reasonable score ~30)
    stars = min(5.0, max(0.0, raw_score / 6))
    return round(stars, 1)


def _bytes_to_buffer(image_bytes: bytes) -> io.BytesIO:
    """Convert bytes to a seekable buffer."""
    buffer = io.BytesIO(image_bytes)
    buffer.seek(0)
    return buffer


async def _fetch_avatar(url: str) -> Optional[Image.Image]:
    """Fetch avatar image from URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(_bytes_to_buffer(data)).convert("RGBA")
    except Exception as e:
        logger.warning(f"Failed to fetch avatar: {e}")
    return None


def _draw_gradient(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    top_color: Tuple[int, int, int],
    bottom_color: Tuple[int, int, int],
) -> None:
    """Draw a vertical gradient."""
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    bbox: Tuple[int, int, int, int],
    radius: int,
    fill: Tuple[int, int, int, int],
) -> None:
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = bbox
    draw.rounded_rectangle(bbox, radius=radius, fill=fill)


def _draw_avatar_glow(
    card: Image.Image,
    center: Tuple[int, int],
    radius: int,
    glow_color: Tuple[int, int, int],
) -> None:
    """Draw a subtle glow behind the avatar."""
    glow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    
    # Draw multiple circles with decreasing opacity for glow effect
    for i in range(20, 0, -2):
        opacity = int(60 * (1 - i / 20))
        glow_draw.ellipse(
            [
                center[0] - radius - i,
                center[1] - radius - i,
                center[0] + radius + i,
                center[1] + radius + i,
            ],
            fill=(*glow_color, opacity),
        )
    
    # Blur the glow
    glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
    card.paste(glow, (0, 0), glow)


def _draw_stars(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    rating: float,
    star_color: Tuple[int, int, int],
    size: int = 18,
) -> None:
    """Draw star rating."""
    full_stars = int(rating)
    partial = rating - full_stars
    
    for i in range(5):
        star_x = x + i * (size + 4)
        
        if i < full_stars:
            # Full star
            _draw_star(draw, star_x, y, size, star_color)
        elif i == full_stars and partial >= 0.5:
            # Half star
            _draw_star(draw, star_x, y, size, star_color, half=True)
        else:
            # Empty star
            _draw_star(draw, star_x, y, size, (80, 80, 80))


def _draw_star(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    color: Tuple[int, int, int],
    half: bool = False,
) -> None:
    """Draw a single star."""
    import math
    
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = size if i % 2 == 0 else size * 0.4
        px = x + size + r * math.cos(angle)
        py = y + size - r * math.sin(angle)
        points.append((px, py))
    
    if half:
        # Draw half star by masking
        draw.polygon(points, fill=color)
        # Cover right half with dark
        draw.rectangle(
            [x + size, y, x + size * 2 + 2, y + size * 2 + 2],
            fill=(80, 80, 80),
        )
    else:
        draw.polygon(points, fill=color)


def _draw_stat_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    label: str,
    value: str,
    theme: Dict[str, Any],
    accent_strip: bool = True,
) -> None:
    """Draw a stat badge with optional accent strip."""
    badge_bg = theme["badge_bg"]
    accent = theme["accent"]
    text_primary = theme["text_primary"]
    text_secondary = theme["text_secondary"]
    
    # Badge background
    _draw_rounded_rect(draw, (x, y, x + width, y + height), 8, badge_bg)
    
    # Accent strip on left
    if accent_strip:
        draw.rectangle([x, y + 4, x + 3, y + height - 4], fill=accent)
    
    # Label (smaller, secondary color)
    label_bbox = draw.textbbox((0, 0), label, font=_font_small)
    label_width = label_bbox[2] - label_bbox[0]
    draw.text(
        (x + (width - label_width) // 2, y + 6),
        label,
        font=_font_small,
        fill=text_secondary,
    )
    
    # Value (larger, primary color)
    value_bbox = draw.textbbox((0, 0), value, font=_font_bold_medium)
    value_width = value_bbox[2] - value_bbox[0]
    draw.text(
        (x + (width - value_width) // 2, y + 26),
        value,
        font=_font_bold_medium,
        fill=text_primary,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

async def generate_player_card(
    player_name: str,
    team_name: str,
    team_code: str,
    league_type: str,
    avatar_url: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
) -> bytes:
    """
    Generate a player card image.
    
    Args:
        player_name: Player's display name
        team_name: Team's full name
        team_code: Team's code (e.g., "PANDAS")
        league_type: League type (NBL, MBL, NCBA)
        avatar_url: URL to player's avatar image
        stats: Player stats dict with ppg, games, avg_goals, avg_assists, avg_saves, total_points
        use_cache: Whether to use cached cards
    
    Returns:
        PNG image bytes
    """
    _load_fonts()
    
    # Check cache
    cache_key = f"{player_name}:{team_code}:{league_type}:{stats}"
    if use_cache and cache_key in _card_cache:
        logger.debug(f"Cache hit for player card: {player_name}")
        return _card_cache[cache_key]
    
    stats = stats or {}
    theme = _get_theme(league_type)
    
    # Create card
    card = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 255))
    draw = ImageDraw.Draw(card)
    
    # Draw gradient background
    _draw_gradient(
        draw,
        CARD_WIDTH,
        CARD_HEIGHT,
        theme["gradient_top"],
        theme["gradient_bottom"],
    )
    
    # Avatar section
    avatar_center = (CARD_WIDTH // 2, 130)
    avatar_radius = 70
    
    # Draw glow behind avatar
    _draw_avatar_glow(card, avatar_center, avatar_radius, theme["accent"])
    
    # Fetch and draw avatar
    avatar = await _fetch_avatar(avatar_url) if avatar_url else None
    
    if avatar:
        # Resize avatar
        avatar = avatar.resize((avatar_radius * 2, avatar_radius * 2), Image.LANCZOS)
        
        # Create circular mask
        mask = Image.new("L", (avatar_radius * 2, avatar_radius * 2), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, avatar_radius * 2, avatar_radius * 2], fill=255)
        
        # Apply mask and paste
        avatar_pos = (avatar_center[0] - avatar_radius, avatar_center[1] - avatar_radius)
        card.paste(avatar, avatar_pos, mask)
        
        # Draw border around avatar
        draw = ImageDraw.Draw(card)
        draw.ellipse(
            [
                avatar_center[0] - avatar_radius - 2,
                avatar_center[1] - avatar_radius - 2,
                avatar_center[0] + avatar_radius + 2,
                avatar_center[1] + avatar_radius + 2,
            ],
            outline=theme["accent"],
            width=3,
        )
    else:
        # Placeholder circle
        draw.ellipse(
            [
                avatar_center[0] - avatar_radius,
                avatar_center[1] - avatar_radius,
                avatar_center[0] + avatar_radius,
                avatar_center[1] + avatar_radius,
            ],
            fill=theme["badge_bg"],
            outline=theme["accent"],
            width=2,
        )
        # Question mark
        draw.text(
            (avatar_center[0] - 15, avatar_center[1] - 20),
            "?",
            font=_font_bold_large,
            fill=theme["text_secondary"],
        )
    
    # Player name
    name_bbox = draw.textbbox((0, 0), player_name, font=_font_bold_large)
    name_width = name_bbox[2] - name_bbox[0]
    draw.text(
        ((CARD_WIDTH - name_width) // 2, 215),
        player_name,
        font=_font_bold_large,
        fill=theme["text_primary"],
    )
    
    # Team info
    team_text = f"{team_code} • {league_type}"
    team_bbox = draw.textbbox((0, 0), team_text, font=_font_regular)
    team_width = team_bbox[2] - team_bbox[0]
    draw.text(
        ((CARD_WIDTH - team_width) // 2, 255),
        team_text,
        font=_font_regular,
        fill=theme["text_secondary"],
    )
    
    # Star rating
    star_rating = _calculate_star_rating(stats)
    star_width = 5 * 22  # 5 stars * (18 size + 4 gap)
    _draw_stars(
        draw,
        (CARD_WIDTH - star_width) // 2,
        285,
        star_rating,
        theme["star_color"],
    )
    
    # Stats grid (3x2)
    stat_labels = [
        ("PPG", f"{stats.get('ppg', 0):.1f}"),
        ("GAMES", str(stats.get("games", 0))),
        ("GOALS", f"{stats.get('avg_goals', 0):.1f}"),
        ("ASSISTS", f"{stats.get('avg_assists', 0):.1f}"),
        ("SAVES", f"{stats.get('avg_saves', 0):.1f}"),
        ("POINTS", str(stats.get("total_points", 0))),
    ]
    
    badge_width = 110
    badge_height = 55
    badge_margin = 10
    grid_start_x = (CARD_WIDTH - (3 * badge_width + 2 * badge_margin)) // 2
    grid_start_y = 325
    
    for i, (label, value) in enumerate(stat_labels):
        row = i // 3
        col = i % 3
        x = grid_start_x + col * (badge_width + badge_margin)
        y = grid_start_y + row * (badge_height + badge_margin)
        _draw_stat_badge(draw, x, y, badge_width, badge_height, label, value, theme)
    
    # League watermark at bottom
    watermark = f"{league_type} PLAYER CARD"
    wm_bbox = draw.textbbox((0, 0), watermark, font=_font_small)
    wm_width = wm_bbox[2] - wm_bbox[0]
    draw.text(
        ((CARD_WIDTH - wm_width) // 2, CARD_HEIGHT - 25),
        watermark,
        font=_font_small,
        fill=(*theme["text_secondary"][:3], 100) if len(theme["text_secondary"]) == 3 else theme["text_secondary"],
    )
    
    # Export to bytes
    buffer = io.BytesIO()
    card.save(buffer, format="PNG", optimize=True)
    image_bytes = buffer.getvalue()
    
    # Cache the result
    if use_cache:
        _card_cache[cache_key] = image_bytes
    
    logger.info(f"Generated player card for {player_name} ({league_type})")
    return image_bytes


def clear_card_cache() -> int:
    """Clear the card cache. Returns number of items cleared."""
    count = len(_card_cache)
    _card_cache.clear()
    return count


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return {
        "size": len(_card_cache),
        "maxsize": _card_cache.maxsize,
        "ttl": _card_cache.ttl,
    }
