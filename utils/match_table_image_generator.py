"""Match table image: a grid of every game in a division, one row and one column per team.

Each cell holds one slot per game between the two teams: the first is the row team's home game, the second its
away game, and so on. Scores are shown from the row team's side, green for a win, red for a loss, grey when the
game has not been played.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.render import canvas
from utils.render.fonts import font
from utils.render.theme import CARD, DIVIDER, LOSS, MUTED, PENDING, TEXT, WIN
from utils.render.theme import accent_color as resolve_accent

logger = logging.getLogger(__name__)

INNER_PAD = 22
HEADER_HEIGHT = 76
LOGO_BAND = 64  # width of the row-logo column and height of the column-logo row
CELL_WIDTH = 76
TEAM_LOGO_SIZE = 36
LEAGUE_LOGO_SIZE = 44
BOTTOM_PAD = 14
MIN_CONTENT_WIDTH = 400  # keeps the header readable when there are only a few teams

# Cell layout for a few common numbers of games per pairing
GAMES_PER_MATCHUP_CONFIGS = {
    2: {"subcells": 2, "cell_height": 70, "layout": "vertical"},   # 1 home, 1 away (default)
    3: {"subcells": 3, "cell_height": 84, "layout": "vertical"},   # home, away, home
    4: {"subcells": 4, "cell_height": 96, "layout": "vertical"},   # 2 home, 2 away
    6: {"subcells": 6, "cell_height": 108, "layout": "grid"},      # 3 rows of home/away
}

match_table_cache = canvas.RenderCache()


def matchup_layout(games_per_matchup: int) -> Dict[str, Any]:
    """Cell layout for any number of games per pairing (1-10): a vertical stack up to four games,
    then a two-column grid."""
    games = max(1, min(int(games_per_matchup or 2), 10))
    if games in GAMES_PER_MATCHUP_CONFIGS:
        return GAMES_PER_MATCHUP_CONFIGS[games]
    if games == 1:
        return {"subcells": 1, "cell_height": 70, "layout": "vertical"}
    rows = (games + 1) // 2
    return {"subcells": games, "cell_height": 36 * rows, "layout": "grid"}


def _slot_rects(x: int, y: int, config: Dict[str, Any]) -> List[Tuple[int, int, int, int]]:
    """The rectangle of each game slot of a cell whose top-left corner is (x, y)."""
    slots, height = config["subcells"], config["cell_height"]
    if config["layout"] == "grid":
        rows, width = (slots + 1) // 2, CELL_WIDTH // 2
        slot_height = height // rows
        return [
            (x + (i % 2) * width, y + (i // 2) * slot_height, x + (i % 2 + 1) * width, y + (i // 2 + 1) * slot_height)
            for i in range(slots)
        ]
    slot_height = height // slots
    return [(x, y + i * slot_height, x + CELL_WIDTH, y + (i + 1) * slot_height) for i in range(slots)]


def _score_font(slot_width: int, slot_height: int):
    """The largest bold font in which a typical score still fits its slot."""
    for size in range(15, 8, -1):
        candidate = font(size, bold=True)
        if candidate.getlength("00–00") <= slot_width - 6 and size + 6 <= slot_height:
            return candidate
    return font(9, bold=True)


def _result_for(row_is_home: bool, match: Optional[Dict[str, Any]]) -> Optional[Tuple[str, bool]]:
    """('their–our' score text from the row team's side, whether the row team won) for a played game."""
    if not match or match.get("status") != "final":
        return None
    home, away = match.get("team1_score") or 0, match.get("team2_score") or 0
    ours, theirs = (home, away) if row_is_home else (away, home)
    return f"{ours}–{theirs}", ours > theirs


def generate_match_table_image(
    matches: List[Dict[str, Any]],
    teams: List[Dict[str, Any]],
    season_name: str,
    league_name: str,
    league_code: Optional[str] = None,
    games_per_matchup: int = 2,
) -> Optional[io.BytesIO]:
    """Render a division's games as a PNG grid, or None when there are no teams.

    ``matches`` need ``team1_id`` (home), ``team2_id``, ``team1_score``, ``team2_score`` and ``status``;
    ``teams`` need ``id``, ``code`` and ``name``.
    """
    if not teams:
        return None
    try:
        config = matchup_layout(games_per_matchup)
        accent = resolve_accent(None, league_code)
        key = match_table_cache.key({
            "league": [league_code, league_name, season_name], "accent": accent, "games": config["subcells"],
            "teams": [[t.get("id"), t.get("code"), t.get("name")] for t in teams],
            "matches": [[m.get("team1_id"), m.get("team2_id"), m.get("team1_score"), m.get("team2_score"), m.get("status")]
                        for m in matches],
        })
        cached = match_table_cache.get(key)
        if cached is not None:
            return cached

        return match_table_cache.put(key, _render(matches, teams, season_name, league_name, league_code, config, accent))
    except Exception as exc:
        logger.error("Failed to generate match table image: %s", exc, exc_info=True)
        return None


def _render(matches, teams, season_name, league_name, league_code, config, accent):
    cell_height = config["cell_height"]
    grid_width = LOGO_BAND + len(teams) * CELL_WIDTH
    grid_height = LOGO_BAND + len(teams) * cell_height
    content_width = max(grid_width, MIN_CONTENT_WIDTH)
    top = canvas.PADDING + canvas.ACCENT_HEIGHT
    width = canvas.PADDING * 2 + INNER_PAD * 2 + content_width
    height = top + HEADER_HEIGHT + grid_height + BOTTOM_PAD + canvas.PADDING
    image, draw = canvas.new_image(width, height, accent)
    left = canvas.PADDING + INNER_PAD

    # Header
    middle = top + HEADER_HEIGHT // 2
    canvas.draw_logo(image, draw, (left + LEAGUE_LOGO_SIZE // 2, middle), LEAGUE_LOGO_SIZE, [league_code, league_name], league_name)
    text_x = left + LEAGUE_LOGO_SIZE + 14
    text_width = content_width - LEAGUE_LOGO_SIZE - 14
    title = canvas.fit_text(f"{league_name} — {season_name}", font(22, bold=True), text_width)
    subtitle = canvas.fit_text("MATCH RESULTS · SCORES FROM THE ROW TEAM'S SIDE", font(12), text_width)
    draw.text((text_x, middle - 11), title, font=font(22, bold=True), fill=TEXT, anchor="lm")
    draw.text((text_x, middle + 15), subtitle, font=font(12), fill=MUTED, anchor="lm")

    grid_left, grid_top = left + (content_width - grid_width) // 2, top + HEADER_HEIGHT
    # Row and column headers: team logos
    for index, team in enumerate(teams):
        names = [team.get("code"), team.get("name")]
        canvas.draw_logo(image, draw, (grid_left + LOGO_BAND + index * CELL_WIDTH + CELL_WIDTH // 2, grid_top + LOGO_BAND // 2),
                         TEAM_LOGO_SIZE, names, team.get("name"))
        canvas.draw_logo(image, draw, (grid_left + LOGO_BAND // 2, grid_top + LOGO_BAND + index * cell_height + cell_height // 2),
                         TEAM_LOGO_SIZE, names, team.get("name"))

    # Games by (home team, away team)
    played: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for match in matches:
        if match.get("team1_id") and match.get("team2_id"):
            played.setdefault((match["team1_id"], match["team2_id"]), []).append(match)

    slot_rects = _slot_rects(0, 0, config)
    score_font = _score_font(slot_rects[0][2] - slot_rects[0][0], slot_rects[0][3] - slot_rects[0][1])
    for row, row_team in enumerate(teams):
        for col, col_team in enumerate(teams):
            x = grid_left + LOGO_BAND + col * CELL_WIDTH
            y = grid_top + LOGO_BAND + row * cell_height
            if row_team["id"] == col_team["id"]:
                draw.rectangle((x, y, x + CELL_WIDTH - 1, y + cell_height - 1), fill=DIVIDER)
                continue
            home_games = played.get((row_team["id"], col_team["id"]), [])  # the row team hosted these
            away_games = played.get((col_team["id"], row_team["id"]), [])
            for slot, (x0, y0, x1, y1) in enumerate(_slot_rects(x, y, config)):
                row_is_home = slot % 2 == 0
                games = home_games if row_is_home else away_games
                result = _result_for(row_is_home, games[slot // 2] if slot // 2 < len(games) else None)
                fill = PENDING if result is None else WIN if result[1] else LOSS
                draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill, outline=CARD)
                if result:
                    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), result[0], font=score_font, fill=TEXT, anchor="mm")
    return image
