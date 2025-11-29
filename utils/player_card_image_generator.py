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
