"""Colours shared by every generated image: a flat dark look that matches Discord's dark theme.

Only the accent changes per division; everything else is fixed, so all images look like one family.
"""

from __future__ import annotations

from typing import Optional, Tuple

RGB = Tuple[int, int, int]

BG: RGB = (30, 31, 34)
CARD: RGB = (43, 45, 49)
ROW_ALT: RGB = (49, 51, 56)
DIVIDER: RGB = (63, 65, 71)
TEXT: RGB = (242, 243, 245)
MUTED: RGB = (181, 186, 193)
WIN: RGB = (35, 165, 90)
LOSS: RGB = (242, 63, 67)
PENDING: RGB = (56, 58, 64)
DEFAULT_ACCENT: RGB = (88, 101, 242)

# Stripe colour for each standings zone (names from services.standings)
ZONE_COLORS = {
    "champion_playoff": (255, 197, 80),
    "promoted": (46, 204, 113),
    "promotion_playoff": (125, 220, 160),
    "relegation_playoff": (241, 160, 70),
    "relegated": (231, 76, 60),
}


def parse_hex(value: Optional[str]) -> Optional[RGB]:
    """``"#1A2B3C"`` or ``"#abc"`` as an RGB tuple (None when missing or malformed)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def division_color(league_code: Optional[str]) -> Optional[str]:
    """The ``#RRGGBB`` colour of a division looked up by code (or name/tier)."""
    if not league_code:
        return None
    from utils.league_cache import LeagueCache

    return (LeagueCache.find_division(league_code) or {}).get("color")


def accent_color(accent: Optional[str] = None, league_code: Optional[str] = None) -> RGB:
    """The image's accent: the given ``#RRGGBB``, else the division's colour, else the default."""
    return parse_hex(accent) or parse_hex(division_color(league_code)) or DEFAULT_ACCENT
