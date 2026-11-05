"""Player card image: who the player is, this season's stats beside their career totals, and where they have played."""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional, Sequence

from utils.render import canvas
from utils.render.fonts import font
from utils.render.limit import render_in_thread
from utils.render.theme import DIVIDER, MUTED, TEXT, accent_color

logger = logging.getLogger(__name__)

WIDTH = 440
INNER_PAD = 24
HEADER_HEIGHT = 104
COLUMN_TITLE_HEIGHT = 38
STAT_ROW_HEIGHT = 32
FOOTER_HEIGHT = 64
LOGO_SIZE = 60

# (label, key in a stats row) in the order they are shown
STAT_ROWS = (
    ("Goals", "goals"),
    ("Assists", "assists"),
    ("Saves", "saves"),
    ("Blocks", "blocks"),
    ("Points", "points"),
    ("Games", "games_played"),
)

player_card_cache = canvas.RenderCache()


def career_totals(career: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Every stat summed over all seasons."""
    return {key: sum(int(row.get(key) or 0) for row in career) for _, key in STAT_ROWS}


def seasons_played(career: Sequence[Dict[str, Any]]) -> str:
    """'S2' for one season, 'S1 – S4' for several (oldest and newest by season number); empty with no games."""
    ordered = sorted((r for r in career if r.get("season_name")), key=lambda r: r.get("season_number") or 0)
    if not ordered:
        return ""
    first, last = ordered[0]["season_name"], ordered[-1]["season_name"]
    return first if first == last else f"{first} – {last}"


def teams_played(career: Sequence[Dict[str, Any]]) -> List[str]:
    """Names of the teams the player has played for, oldest first, each once."""
    ordered = sorted(career, key=lambda r: r.get("season_number") or 0)
    return list(dict.fromkeys(r["team_name"] for r in ordered if r.get("team_name")))


def generate_player_card_image(
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_code: Optional[str] = None,
    season_name: Optional[str] = None,
    season_stats: Optional[Dict[str, Any]] = None,
    career: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[io.BytesIO]:
    """Render a player's card as a PNG, or None if drawing fails.

    ``season_stats`` is the player's row for the shown season (the keys of ``STAT_ROWS``); ``career`` is one such row
    per season, with ``season_number``, ``season_name`` and ``team_name`` too.
    """
    try:
        career = list(career or [])
        season_stats = season_stats or {}
        accent = accent_color(None, league_code)
        key = player_card_cache.key({
            "who": [player_name, team_name, team_code, league_code, season_name], "accent": accent,
            "season": season_stats, "career": career,
        })
        cached = player_card_cache.get(key)
        if cached is not None:
            return cached
        image = _render(player_name, team_name, team_code, league_code, season_name, season_stats, career, accent)
        return player_card_cache.put(key, image)
    except Exception as exc:
        logger.error("Failed to generate player card: %s", exc, exc_info=True)
        return None


def _render(player_name, team_name, team_code, league_code, season_name, season_stats, career, accent):
    played = seasons_played(career)
    teams = teams_played(career)
    footer_lines = [line for line in (
        f"SEASONS  {played}" if played else "",
        f"TEAMS  {', '.join(teams)}" if teams else "",
    ) if line] or ["No games recorded yet"]

    content_width = WIDTH - 2 * (canvas.PADDING + INNER_PAD)
    top = canvas.PADDING + canvas.ACCENT_HEIGHT
    height = (top + HEADER_HEIGHT + COLUMN_TITLE_HEIGHT + STAT_ROW_HEIGHT * len(STAT_ROWS)
              + FOOTER_HEIGHT + canvas.PADDING)
    image, draw = canvas.new_image(WIDTH, height, accent)
    left = canvas.PADDING + INNER_PAD

    # Header: team logo, name, team and division
    middle = top + HEADER_HEIGHT // 2
    canvas.draw_logo(image, draw, (left + LOGO_SIZE // 2, middle), LOGO_SIZE, [team_code, team_name], team_name or player_name)
    text_x = left + LOGO_SIZE + 16
    text_width = content_width - LOGO_SIZE - 16
    subtitle = " · ".join(part for part in (team_name or "Free agent", (league_code or "").upper()) if part)
    draw.text((text_x, middle - 12), canvas.fit_text(player_name, font(26, bold=True), text_width),
              font=font(26, bold=True), fill=TEXT, anchor="lm")
    draw.text((text_x, middle + 16), canvas.fit_text(subtitle, font(14), text_width), font=font(14), fill=MUTED, anchor="lm")

    # Two columns: this season, and the whole career
    column_width = content_width // 2 - 8
    columns = (
        (left, (season_name or "Season").upper(), season_stats),
        (left + content_width - column_width, "CAREER", career_totals(career)),
    )
    y = top + HEADER_HEIGHT
    draw.line((left + content_width // 2, y + 6, left + content_width // 2, y + COLUMN_TITLE_HEIGHT + STAT_ROW_HEIGHT * len(STAT_ROWS) - 6),
              fill=DIVIDER, width=1)
    for x, title, stats in columns:
        draw.text((x, y + COLUMN_TITLE_HEIGHT // 2), canvas.fit_text(title, font(12, bold=True), column_width),
                  font=font(12, bold=True), fill=MUTED, anchor="lm")
        for row, (label, stat_key) in enumerate(STAT_ROWS):
            row_middle = y + COLUMN_TITLE_HEIGHT + row * STAT_ROW_HEIGHT + STAT_ROW_HEIGHT // 2
            draw.text((x, row_middle), label, font=font(14), fill=MUTED, anchor="lm")
            draw.text((x + column_width, row_middle), str(int(stats.get(stat_key) or 0)), font=font(16, bold=True), fill=TEXT, anchor="rm")

    # Footer: seasons and teams
    y += COLUMN_TITLE_HEIGHT + STAT_ROW_HEIGHT * len(STAT_ROWS)
    draw.line((left, y + 10, left + content_width, y + 10), fill=DIVIDER, width=1)
    for index, line in enumerate(footer_lines[:2]):
        draw.text((left, y + 28 + index * 20), canvas.fit_text(line, font(12), content_width), font=font(12), fill=MUTED, anchor="lm")
    return image


async def generate_player_card(
    *,
    player_name: str,
    team_name: Optional[str] = None,
    team_code: Optional[str] = None,
    league_type: Optional[str] = None,
    season_name: Optional[str] = None,
    season_stats: Optional[Dict[str, Any]] = None,
    career: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[bytes]:
    """The player card as PNG bytes, rendered off the event loop (None if drawing fails)."""
    buffer = await render_in_thread(
        generate_player_card_image, player_name, team_name, team_code, league_type, season_name, season_stats, career
    )
    return buffer.getvalue() if buffer else None
