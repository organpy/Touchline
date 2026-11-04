"""Standings image: one flat dark card per division with rank, team, record, point difference, points and form."""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional

from services.standings import ZONE_LABELS
from utils.render import canvas
from utils.render.fonts import font
from utils.render.theme import CARD, DIVIDER, LOSS, MUTED, ROW_ALT, TEXT, WIN, ZONE_COLORS
from utils.render.theme import accent_color as resolve_accent

logger = logging.getLogger(__name__)

INNER_PAD = 22  # between the card edge and the table
HEADER_HEIGHT = 76
COLUMN_HEIGHT = 34
ROW_HEIGHT = 46
LEGEND_HEIGHT = 40
BOTTOM_PAD = 14

COL_RANK = 48
COL_LOGO = 48
COL_TEAM = 230
COL_RECORD = 84
COL_DIFF = 72
COL_POINTS = 64
COL_FORM = 124

LEAGUE_LOGO_SIZE = 44
TEAM_LOGO_SIZE = 32
ZONE_STRIPE = 5
FORM_DOT_RADIUS = 7
FORM_DOT_SPACING = 22

standings_cache = canvas.RenderCache()


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "0"


def _draw_form(draw, column_left: int, center_y: int, markers: List[str]) -> None:
    """Last results as dots centred in the form column: green win, red loss, hollow for a game not played."""
    markers = markers[:5]
    dots_width = len(markers) * FORM_DOT_SPACING - (FORM_DOT_SPACING - 2 * FORM_DOT_RADIUS)
    x = column_left + (COL_FORM - dots_width) // 2 + FORM_DOT_RADIUS
    for marker in markers:
        box = (x - FORM_DOT_RADIUS, center_y - FORM_DOT_RADIUS, x + FORM_DOT_RADIUS, center_y + FORM_DOT_RADIUS)
        if marker == "W":
            draw.ellipse(box, fill=WIN)
        elif marker == "L":
            draw.ellipse(box, fill=LOSS)
        else:
            draw.ellipse(box, outline=DIVIDER, width=2)
        x += FORM_DOT_SPACING


def _form_for(team: Dict[str, Any], recent_results: Dict[str, List[str]]) -> List[str]:
    team_id = team.get("team_id") or team.get("id")
    return recent_results.get(str(team_id)) or recent_results.get(team_id) or []


def generate_simple_standings_image(
    standings_data: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
    recent_results: Optional[Dict[str, List[str]]] = None,
    accent_color: Optional[str] = None,
) -> Optional[io.BytesIO]:
    """Render one division's standings as a PNG, or None when there is nothing to show.

    ``standings_data`` rows need ``name``, ``code``, ``wins``, ``losses``, ``point_diff``, ``competition_points``,
    ``zone`` and ``team_id``. ``recent_results`` maps a team id to its last results ('W', 'L' or 'N' for none);
    when given, a form column is drawn. ``accent_color`` is the division's ``#RRGGBB``, found from ``league_code``
    when omitted.
    """
    if not standings_data:
        return None
    try:
        rgb = resolve_accent(accent_color, league_code)
        recent = recent_results or {}
        key = standings_cache.key({
            "league": [league_code, league_name, season_name], "accent": rgb, "rows": standings_data, "form": recent,
        })
        cached = standings_cache.get(key)
        if cached is not None:
            return cached

        image = _render(standings_data, season_name, league_name, league_code, recent, rgb)
        return standings_cache.put(key, image)
    except Exception as exc:
        logger.error("Failed to generate standings image: %s", exc, exc_info=True)
        return None


def _render(rows, season_name, league_name, league_code, recent, accent):
    show_form = bool(recent)
    table_width = COL_RANK + COL_LOGO + COL_TEAM + COL_RECORD + COL_DIFF + COL_POINTS + (COL_FORM if show_form else 0)
    zones = list(dict.fromkeys(r["zone"] for r in rows if r.get("zone") in ZONE_COLORS))
    legend = LEGEND_HEIGHT if zones else 0

    width = canvas.PADDING * 2 + INNER_PAD * 2 + table_width
    top = canvas.PADDING + canvas.ACCENT_HEIGHT
    height = top + HEADER_HEIGHT + COLUMN_HEIGHT + ROW_HEIGHT * len(rows) + legend + BOTTOM_PAD + canvas.PADDING
    image, draw = canvas.new_image(width, height, accent)
    left = canvas.PADDING + INNER_PAD

    # Header: division logo, name, season
    middle = top + HEADER_HEIGHT // 2
    canvas.draw_logo(image, draw, (left + LEAGUE_LOGO_SIZE // 2, middle), LEAGUE_LOGO_SIZE, [league_code, league_name], league_name)
    text_x = left + LEAGUE_LOGO_SIZE + 14
    draw.text((text_x, middle - 11), canvas.fit_text(league_name, font(22, bold=True), table_width - LEAGUE_LOGO_SIZE - 14),
              font=font(22, bold=True), fill=TEXT, anchor="lm")
    draw.text((text_x, middle + 15), f"{(season_name or 'Season').upper()} • {len(rows)} TEAMS", font=font(13), fill=MUTED, anchor="lm")

    # Column titles
    y = top + HEADER_HEIGHT
    heads = font(12, bold=True)
    x = left
    draw.text((x + COL_RANK // 2, y + COLUMN_HEIGHT // 2), "#", font=heads, fill=MUTED, anchor="mm")
    x += COL_RANK + COL_LOGO
    draw.text((x + 8, y + COLUMN_HEIGHT // 2), "TEAM", font=heads, fill=MUTED, anchor="lm")
    x += COL_TEAM
    for label, column in (("W–L", COL_RECORD), ("±", COL_DIFF), ("PTS", COL_POINTS)):
        draw.text((x + column // 2, y + COLUMN_HEIGHT // 2), label, font=heads, fill=MUTED, anchor="mm")
        x += column
    if show_form:
        draw.text((x + COL_FORM // 2, y + COLUMN_HEIGHT // 2), "LAST 5", font=heads, fill=MUTED, anchor="mm")
    y += COLUMN_HEIGHT

    # Rows
    body, bold = font(14), font(14, bold=True)
    for index, team in enumerate(rows, start=1):
        row_middle = y + ROW_HEIGHT // 2
        draw.rectangle((left, y, left + table_width, y + ROW_HEIGHT - 1), fill=ROW_ALT if index % 2 == 0 else CARD)
        if team.get("zone") in ZONE_COLORS:
            draw.rectangle((left, y, left + ZONE_STRIPE - 1, y + ROW_HEIGHT - 1), fill=ZONE_COLORS[team["zone"]])

        x = left
        draw.text((x + COL_RANK // 2, row_middle), str(team.get("rank") or index), font=bold, fill=TEXT, anchor="mm")
        x += COL_RANK
        canvas.draw_logo(image, draw, (x + COL_LOGO // 2, row_middle), TEAM_LOGO_SIZE, [team.get("code"), team.get("name")], team.get("name"))
        x += COL_LOGO
        draw.text((x + 8, row_middle), canvas.fit_text(team.get("name") or "Unknown", body, COL_TEAM - 16), font=body, fill=TEXT, anchor="lm")
        x += COL_TEAM
        draw.text((x + COL_RECORD // 2, row_middle), f"{team.get('wins', 0)}–{team.get('losses', 0)}", font=body, fill=TEXT, anchor="mm")
        x += COL_RECORD
        diff = int(team.get("point_diff") or 0)
        draw.text((x + COL_DIFF // 2, row_middle), _signed(diff), font=bold, fill=WIN if diff > 0 else LOSS if diff < 0 else MUTED, anchor="mm")
        x += COL_DIFF
        draw.text((x + COL_POINTS // 2, row_middle), str(team.get("competition_points", 0)), font=bold, fill=TEXT, anchor="mm")
        x += COL_POINTS
        if show_form:
            _draw_form(draw, x, row_middle, _form_for(team, recent))
        y += ROW_HEIGHT

    # Legend: only the zones that appear
    if zones:
        x, legend_middle = left, y + LEGEND_HEIGHT // 2 + 2
        small = font(12)
        for zone in zones:
            draw.rectangle((x, legend_middle - 5, x + 10, legend_middle + 5), fill=ZONE_COLORS[zone])
            label = ZONE_LABELS.get(zone, zone)
            draw.text((x + 16, legend_middle), label, font=small, fill=MUTED, anchor="lm")
            x += 16 + int(small.getlength(label)) + 24
    return image
