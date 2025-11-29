"""
Redesigned Player Card Generator — Premium tier-style cards
Rarity tiers, glow effects, foil border, textures, centered layout.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT_DIR / "images"
FONTS_DIR = ROOT_DIR / "fonts"

# -----------------------------
# Card layout constants
# -----------------------------
CARD_WIDTH = 500
CARD_HEIGHT = 750
PADDING = 26

AVATAR_SIZE = 180
AVATAR_BORDER = 6
AVATAR_GLOW_SIZE = 280

BADGE_HEIGHT = 42
BADGE_RADIUS = 18
BADGE_PAD = 14

STAR_SIZE = 24
STAR_GAP = 6
MAX_STARS = 5


# -----------------------------
# RARITY TIERS (AUTO BASED ON STAR RATING)
# -----------------------------

RARITY = {
    "common": {
        "bg": ((54, 83, 255), (14, 25, 80)),     # blue
        "foil": (120, 140, 255),
        "badge": (25, 40, 80),
        "accent": (66, 114, 255),
    },
    "uncommon": {
        "bg": ((0, 190, 120), (4, 60, 36)),       # green
        "foil": (0, 255, 160),
        "badge": (12, 54, 36),
        "accent": (0, 200, 140),
    },
    "rare": {
        "bg": ((131, 0, 255), (43, 0, 84)),       # purple
        "foil": (190, 140, 255),
        "badge": (40, 0, 68),
        "accent": (170, 90, 255),
    },
    "epic": {
        "bg": ((255, 0, 111), (90, 0, 33)),       # magenta
        "foil": (255, 110, 180),
        "badge": (80, 0, 28),
        "accent": (255, 70, 150),
    },
    "legendary": {
        "bg": ((255, 215, 50), (125, 80, 5)),     # gold
        "foil": (255, 240, 170),
        "badge": (90, 60, 20),
        "accent": (255, 210, 100),
    }
}


def rarity_from_stars(stars: float) -> str:
    if stars >= 4.5:
        return "legendary"
    elif stars >= 3.5:
        return "epic"
    elif stars >= 2.5:
        return "rare"
    elif stars >= 1.5:
        return "uncommon"
    return "common"

# ============================================================
#                   COLOR / DRAWING HELPERS
# ============================================================

def to_rgba(c):
    if len(c) == 4:
        return c
    return (c[0], c[1], c[2], 255)


def lerp(a, b, t):
    return int(a + (b - a) * t)


def lerp_color(c1, c2, t):
    return (
        lerp(c1[0], c2[0], t),
        lerp(c1[1], c2[1], t),
        lerp(c1[2], c2[2], t)
    )


# ============================================================
#                         TEXTURES
# ============================================================

def generate_noise_texture(size: Tuple[int, int], opacity=30):
    """Procedural grain texture (prevents 'flat UI' look)."""
    import random

    w, h = size
    tex = Image.new("L", (w, h))
    px = tex.load()

    for y in range(h):
        for x in range(w):
            px[x, y] = random.randint(0, opacity)

    return tex


def apply_texture(canvas: Image.Image, opacity=40):
    """Overlay subtle texture."""
    noise = generate_noise_texture(canvas.size, opacity)
    noise = noise.convert("RGBA")
    return Image.alpha_composite(canvas, noise)


# ============================================================
#                     GRADIENT BACKGROUND
# ============================================================

def vertical_gradient(size, top_color, bottom_color):
    w, h = size
    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)

    for i in range(h):
        t = i / (h - 1)
        col = lerp_color(top_color, bottom_color, t)
        draw.line((0, i, w, i), fill=col)

    return img


# ============================================================
#                      FOIL BORDER (TIER)
# ============================================================

def draw_foil_border(canvas, color=(255, 200, 120), width=8):
    """Creates a glowing foil border using blur + highlight."""
    w, h = canvas.size

    border = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(border)

    # Outer glow
    for i in range(width * 2):
        alpha = int(160 * (1 - i / (width * 2)))
        draw.rounded_rectangle(
            (i, i, w - i, h - i),
            radius=24,
            outline=(*color, alpha),
            width=1
        )

    border = border.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(border)

    # Inner clean line
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (width // 2, width // 2, w - width // 2, h - width // 2),
        radius=22,
        outline=color,
        width=2
    )


# ============================================================
#                 AVATAR GLOW + SHADOW SYSTEM
# ============================================================

def draw_avatar_glow(canvas, center, glow_color, size):
    """Glowy radial halo behind avatar."""
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = size // 2, size // 2
    r = size // 2

    for i in range(r, 0, -1):
        t = i / r
        alpha = int(120 * t)
        col = (glow_color[0], glow_color[1], glow_color[2], alpha)
        gdraw.ellipse((cx - i, cy - i, cx + i, cy + i), fill=col)

    glow = glow.filter(ImageFilter.GaussianBlur(25))

    gx = center[0] - size // 2
    gy = center[1] - size // 2
    canvas.alpha_composite(glow, (gx, gy))


def draw_avatar_ring(canvas, pos, size, border_color):
    """Draws outer ring + inner white circle."""
    draw = ImageDraw.Draw(canvas)

    x, y = pos
    draw.ellipse(
        (x, y, x + size, y + size),
        fill=border_color
    )

    inner = AVATAR_BORDER
    draw.ellipse(
        (x + inner, y + inner, x + size - inner, y + size - inner),
        fill=(255, 255, 255, 255)
    )


# ============================================================
#                  STAR DRAWING (CLEAN VERSION)
# ============================================================

def draw_star(draw: ImageDraw.ImageDraw, pos, size, color, half=False):
    """Sharp symmetrical star (looks good even at small size)."""
    import math

    x, y = pos
    cx = x + size / 2
    cy = y + size / 2
    r_outer = size / 2
    r_inner = r_outer * 0.45

    pts = []
    for i in range(10):
        angle = math.pi/2 + (i * math.pi/5)
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((
            cx + r * math.cos(angle),
            cy - r * math.sin(angle)
        ))

    if half:
        # Clip left half (for half-star)
        pts = [(min(px, cx), py) for px, py in pts]

    draw.polygon(pts, fill=color)


def draw_star_row(draw, x, y, stars, filled_color, empty_color):
    """Draw up to 5 stars, supporting half-star."""
    full = int(stars)
    half = stars - full >= 0.5

    for i in range(MAX_STARS):
        sx = x + i*(STAR_SIZE + STAR_GAP)

        if i < full:
            draw_star(draw, (sx, y), STAR_SIZE, filled_color)
        elif i == full and half:
            draw_star(draw, (sx, y), STAR_SIZE, filled_color, half=True)
            draw_star(draw, (sx, y), STAR_SIZE, empty_color, half=False)
        else:
            draw_star(draw, (sx, y), STAR_SIZE, empty_color)

# ============================================================
#                    Part 3 — FINAL LAYOUT
# ============================================================

from functools import lru_cache
import json
import hashlib
import io
from typing import Dict, Any, Optional, Tuple
from pathlib import Path
from PIL import ImageFont

# -----------------------------
# Simple in-memory cache (bytes)
# -----------------------------
_image_cache: Dict[str, bytes] = {}

# -----------------------------
# Helper: image index / logos
# -----------------------------
@lru_cache(maxsize=1)
def _image_index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    if not IMAGES_DIR.exists():
        return index
    for p in IMAGES_DIR.iterdir():
        if p.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        key = re.sub(r"[^a-z0-9]", "", p.stem.lower())
        index[key] = p
    return index


def _find_logo_path(*candidates: Optional[str]) -> Optional[Path]:
    idx = _image_index()
    for c in candidates:
        if not c:
            continue
        key = re.sub(r"[^a-z0-9]", "", c.lower())
        if key in idx:
            return idx[key]
    return None


def _load_logo(path: Path, size: int) -> Optional[Image.Image]:
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - img.width) // 2, (size - img.height) // 2)
        canvas.paste(img, offset, img)
        return canvas
    except Exception:
        logger.exception("Failed to load logo %s", path)
        return None


# -----------------------------
# Star / rating calculation (simple & pluggable)
# -----------------------------
def calculate_star_rating(stats: Dict[str, Any]) -> float:
    # Keep your original formula: ppg weighted heavily + per-game contributions
    ppg = float(stats.get("ppg", 0) or 0)
    goals = float(stats.get("total_goals", 0) or 0)
    assists = float(stats.get("total_assists", 0) or 0)
    saves = float(stats.get("total_saves", 0) or 0)
    games = float(stats.get("games_played", 0) or 0)

    if games <= 0:
        return 0.5

    avg_goals = goals / games
    avg_assists = assists / games
    avg_saves = saves / games

    score = (ppg * 0.5) + (avg_goals * 3) + (avg_assists * 2) + (avg_saves * 1)
    raw = min(5.0, max(0.5, score / 4.0))
    return round(raw * 2) / 2.0


# -----------------------------
# Font loader (single place)
# -----------------------------
@lru_cache(maxsize=1)
def _load_fonts():
    try:
        # Use robust sizes for the redesigned card
        name_font = ImageFont.truetype(str(FONTS_DIR / "BebasNeue-Regular.ttf"), 40)
        team_font = ImageFont.truetype(str(FONTS_DIR / "Montserrat-Regular.ttf"), 18)
        stat_font = ImageFont.truetype(str(FONTS_DIR / "Montserrat-SemiBold.ttf"), 20)
        badge_font = ImageFont.truetype(str(FONTS_DIR / "Montserrat-Regular.ttf"), 16)
    except Exception:
        # Fallback
        logger.warning("Custom fonts not found in %s — falling back to defaults", FONTS_DIR)
        name_font = ImageFont.load_default()
        team_font = ImageFont.load_default()
        stat_font = ImageFont.load_default()
        badge_font = ImageFont.load_default()
    return name_font, team_font, stat_font, badge_font


# -----------------------------
# Small vignette for depth
# -----------------------------
def apply_vignette(canvas: Image.Image, intensity: float = 0.6):
    w, h = canvas.size
    vign = Image.new("L", (w, h), 0)
    vdraw = ImageDraw.Draw(vign)
    # Draw gradient by concentric ellipses (cheap & effective)
    steps = max(w, h) // 2
    for i in range(steps):
        alpha = int(255 * (i / steps) ** 2 * intensity)
        vdraw.ellipse((i, i, w - i, h - i), outline=alpha)
    vign = vign.filter(ImageFilter.GaussianBlur(8))
    # Apply as darkening layer
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dark.putalpha(vign)
    return Image.alpha_composite(canvas, dark)


# -----------------------------
# Build cache key helper
# -----------------------------
def _make_cache_key(player_id: str, player_name: str, team_code: Optional[str], league_code: Optional[str], stats: Dict[str, Any], star_rating: float):
    payload = {
        "player_id": player_id,
        "player_name": player_name,
        "team_code": (team_code or "").upper(),
        "league_code": (league_code or "").upper(),
        "stats": stats,
        "star_rating": star_rating,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# -----------------------------
# Final card generator
# -----------------------------
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
    Generate the final premium player card (PNG bytes in BytesIO).
    Preserves caching, logos and stat logic.
    """
    try:
        if stats is None:
            stats = {}

        # compute star rating if not provided
        if star_rating is None:
            star_rating = calculate_star_rating(stats)

        cache_key = _make_cache_key(player_id, player_name, team_code, league_code, stats, star_rating)
        if cache_key in _image_cache:
            logger.debug("cache hit for %s", player_name)
            return _bytes_to_buffer(_image_cache[cache_key])

        # fonts
        name_font, team_font, stat_font, badge_font = _load_fonts()

        # rarity theme
        tier = rarity_from_stars(star_rating)
        tier_conf = RARITY.get(tier, RARITY["common"])
        bg_top, bg_bot = tier_conf["bg"]
        foil_color = tier_conf["foil"]
        badge_bg = tier_conf["badge"]
        accent = tier_conf["accent"]

        # canvas and gradient background
        canvas = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))

        bg = vertical_gradient((CARD_WIDTH, CARD_HEIGHT), bg_top, bg_bot)
        canvas.paste(bg, (0, 0), bg)

        # subtle texture overlay
        textured = apply_texture(canvas, opacity=25)
        canvas = textured

        draw = ImageDraw.Draw(canvas)

        # foil border
        draw_foil_border(canvas, color=foil_color, width=10)

        # avatar center coords
        avatar_x = (CARD_WIDTH - AVATAR_SIZE) // 2
        avatar_y = PADDING + 10

        # avatar glow behind
        glow_color = accent if isinstance(accent, tuple) else (255, 255, 255)
        draw_avatar_glow(canvas, (avatar_x + AVATAR_SIZE // 2, avatar_y + AVATAR_SIZE // 2), glow_color, AVATAR_GLOW_SIZE)

        # avatar ring (border + white inner)
        draw_avatar_ring(canvas, (avatar_x, avatar_y), AVATAR_SIZE, border_color=to_rgba(foil_color))

        # Optionally paste a user-supplied avatar image if exists in images dir by player_id or player_name
        avatar_logo_path = _find_logo_path(player_id, player_name)
        if avatar_logo_path:
            try:
                av = Image.open(avatar_logo_path).convert("RGBA")
                av.thumbnail((AVATAR_SIZE - AVATAR_BORDER * 2, AVATAR_SIZE - AVATAR_BORDER * 2), Image.LANCZOS)
                # create circular mask
                mask = Image.new("L", av.size, 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.ellipse((0, 0, av.width, av.height), fill=255)
                paste_x = avatar_x + AVATAR_BORDER + (AVATAR_SIZE - AVATAR_BORDER*2 - av.width) // 2
                paste_y = avatar_y + AVATAR_BORDER + (AVATAR_SIZE - AVATAR_BORDER*2 - av.height) // 2
                canvas.paste(av, (paste_x, paste_y), mask)
            except Exception:
                logger.exception("failed to paste avatar from %s", avatar_logo_path)

        # Player name (centered)
        name_bbox = draw.textbbox((0, 0), player_name, font=name_font)
        name_w = name_bbox[2] - name_bbox[0]
        name_x = (CARD_WIDTH - name_w) // 2
        name_y = avatar_y + AVATAR_SIZE + 14
        draw.text((name_x, name_y), player_name, fill=(255, 255, 255, 255), font=name_font)

        # Team row (centered with optional logo)
        team_row_y = name_y + 44
        team_logo_img = None
        if team_code:
            path = _find_logo_path(team_code)
            if path:
                team_logo_img = _load_logo(path, 36)

        team_text = team_name or ""
        team_text_bbox = draw.textbbox((0, 0), team_text, font=team_font)
        ttw = team_text_bbox[2] - team_text_bbox[0]
        gap = 10
        row_width = ttw + (team_logo_img.width + gap if team_logo_img else 0)
        row_x = (CARD_WIDTH - row_width) // 2

        if team_logo_img:
            canvas.paste(team_logo_img, (row_x, team_row_y - 4), team_logo_img)
            draw.text((row_x + team_logo_img.width + gap, team_row_y), team_text, fill=(240, 240, 240, 230), font=team_font)
        else:
            draw.text((row_x, team_row_y), team_text, fill=(240, 240, 240, 230), font=team_font)

        # Star row centered
        star_total_w = MAX_STARS * STAR_SIZE + (MAX_STARS - 1) * STAR_GAP
        star_x = (CARD_WIDTH - star_total_w) // 2
        star_y = team_row_y + 36
        # draw stars on a temporary draw (so half-star composite works well)
        draw_star_row(draw, star_x, star_y, star_rating, filled_color=to_rgba((255, 230, 120)), empty_color=to_rgba((100, 100, 100)))

        # Stats grid (3 rows × 2 columns)
        stats_y_start = star_y + STAR_SIZE + 30
        col_gap = 16
        col_w = (CARD_WIDTH - PADDING * 2 - col_gap) // 2
        left_x = PADDING
        right_x = PADDING + col_w + col_gap
        row_h = BADGE_HEIGHT + 12

        ppg = stats.get("ppg", 0) or 0
        games = stats.get("games_played", 0) or 0
        total_points = stats.get("total_points", 0) or 0
        total_goals = stats.get("total_goals", 0) or 0
        total_assists = stats.get("total_assists", 0) or 0
        total_saves = stats.get("total_saves", 0) or 0

        badges = [
            (f"PPG: {ppg:.1f}", left_x, stats_y_start),
            (f"Games: {games}", right_x, stats_y_start),
            (f"Goals: {total_goals}", left_x, stats_y_start + row_h),
            (f"Assists: {total_assists}", right_x, stats_y_start + row_h),
            (f"Saves: {total_saves}", left_x, stats_y_start + row_h * 2),
            (f"Points: {total_points}", right_x, stats_y_start + row_h * 2),
        ]

        # draw badges with left accent and small icon
        for text, bx, by in badges:
            # badge bg
            bw = col_w
            draw.rounded_rectangle((bx, by, bx + bw, by + BADGE_HEIGHT), radius=BADGE_RADIUS, fill=to_rgba(badge_bg))
            # left accent strip
            accent_w = 6
            draw.rounded_rectangle((bx, by, bx + accent_w, by + BADGE_HEIGHT), radius=BADGE_RADIUS, fill=to_rgba(accent))
            # draw text
            tbbox = draw.textbbox((0, 0), text, font=badge_font)
            tw = tbbox[2] - tbbox[0]
            th = tbbox[3] - tbbox[1]
            tx = bx + accent_w + 12
            ty = by + (BADGE_HEIGHT - th) // 2
            draw.text((tx, ty), text, fill=(255, 255, 255, 230), font=badge_font)

        # small rarity label at bottom center
        tier_label = tier.upper()
        tl_bbox = draw.textbbox((0, 0), tier_label, font=team_font)
        tllw = tl_bbox[2] - tl_bbox[0]
        tlx = (CARD_WIDTH - tllw) // 2
        tly = CARD_HEIGHT - PADDING - 28
        # small rounded pill
        pill_w = tllw + 28
        pill_x = (CARD_WIDTH - pill_w) // 2
        draw.rounded_rectangle((pill_x, tly - 6, pill_x + pill_w, tly + 26), radius=14, fill=to_rgba(foil_color))
        draw.text((tlx, tly), tier_label, fill=(24, 16, 8, 255), font=team_font)

        # final vignette for depth
        canvas = apply_vignette(canvas, intensity=0.55)

        # save to buffer
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        image_bytes = buf.getvalue()

        # cache
        _image_cache[cache_key] = image_bytes

        return _bytes_to_buffer(image_bytes)

    except Exception as exc:
        logger.exception("Failed to generate premium card for %s: %s", player_name, exc)
        return None
