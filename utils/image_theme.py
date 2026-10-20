"""Division-tinted themes for the generated images (standings, match table, player card).

Every image starts from its ``BASE_THEME``; when the division has a colour, the canvas, header,
borders and accents are tinted with it so each division keeps its own look.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

RGB = Tuple[int, int, int]


def parse_hex(value: Optional[str]) -> Optional[RGB]:
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


def mix(a: Tuple[int, ...], b: Tuple[int, ...], weight: float) -> RGB:
    """``weight`` of ``a`` blended with ``1 - weight`` of ``b``."""
    return tuple(round(a[i] * weight + b[i] * (1 - weight)) for i in range(3))  # type: ignore[return-value]


def _rgba(rgb: Tuple[int, ...], alpha: int = 255) -> Tuple[int, int, int, int]:
    return rgb[0], rgb[1], rgb[2], alpha


def division_color(league_code: Optional[str]) -> Optional[str]:
    """The ``#RRGGBB`` colour of a division looked up by code (or name/tier)."""
    if not league_code:
        return None
    from utils.league_cache import LeagueCache

    return (LeagueCache.find_division(league_code) or {}).get("color")


def tinted_theme(base: Dict[str, Any], accent: Optional[str]) -> Dict[str, Any]:
    """Copy of ``base`` tinted with the ``#RRGGBB`` accent (unchanged when there is none)."""
    theme = copy.deepcopy(base)
    rgb = parse_hex(accent)
    if rgb is None:
        return theme
    white, black = (255, 255, 255), (0, 0, 0)
    canvas_top, canvas_bottom = base["canvas_gradient"]
    theme["canvas_gradient"] = (mix(rgb, canvas_top, 0.12), mix(rgb, canvas_bottom, 0.28))
    theme["header_gradient"] = (mix(rgb, white, 0.85), mix(rgb, black, 0.6))
    theme["card_border"] = _rgba(mix(rgb, base["card_border"], 0.45))
    if "accent_color" in theme:
        theme["accent_color"] = _rgba(rgb)
    if "row_divider" in theme:
        theme["row_divider"] = _rgba(mix(rgb, base["row_divider"], 0.25))
    if "cell_border" in theme:
        theme["cell_border"] = _rgba(mix(rgb, base["cell_border"], 0.25))
    return theme
