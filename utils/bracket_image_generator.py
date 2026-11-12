"""Bracket image: a playoff bracket drawn as a tree, in the same flat dark style as the other images.

Rounds run left to right. Each series is a two-row box (seed, logo, team, series wins); lines join a series to
the slot it feeds in the next round. The last column holds what the bracket decides (champion, promoted or
relegated team). A promotion or relegation bracket with several spots is drawn as one tree per spot, stacked.

Colour is kept for meaning: the division accent marks the team that went through, and a feeding line brightens
once its series is decided. Everything else is grey on grey.

Series rows use the database shape (``brackets.series``): ``id``, ``round``, ``position``, ``team1_id``,
``team2_id``, ``team1_seed``, ``team2_seed``, ``team1_wins``, ``team2_wins``, ``best_of``, ``winner_id``,
``loser_id``, ``forced_winner_id``, ``winner_to``/``winner_slot`` and ``loser_to``/``loser_slot``.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION, round_name
from utils.render import canvas
from utils.render.fonts import font
from utils.render.theme import DIVIDER, FAINT, HAIRLINE, MUTED, ROW_ALT, TEXT
from utils.render.theme import accent_color as resolve_accent

logger = logging.getLogger(__name__)

INNER_PAD = 22
HEADER_HEIGHT = 64
ROUND_LABEL_HEIGHT = 30
SECTION_LABEL_HEIGHT = 24
BOTTOM_PAD = 18

BOX_WIDTH = 216
ROW_HEIGHT = 30
BOX_HEIGHT = ROW_HEIGHT * 2
BOX_GAP = 18  # vertical space between two boxes in the first column
COLUMN_GAP = 44  # horizontal space for the connector lines
SECTION_GAP = 26  # between the trees of a multi-spot bracket
RESULT_WIDTH = 176
RESULT_HEIGHT = 36

SEED_WIDTH = 26
TEAM_LOGO_SIZE = 18
SCORE_WIDTH = 30
MARK_WIDTH = 3  # accent bar on the row of the team that went through
MIN_CONTENT_WIDTH = 420

RESULT_LABELS = {
    KIND_CHAMPIONSHIP: "Champion",
    KIND_PROMOTION: "Promoted",
    KIND_RELEGATION: "Relegated",
    KIND_PROMO_REL: "Upper division",
}

bracket_cache = canvas.RenderCache()


# ------------------------------------------------------------------------------------- layout
@dataclass
class Placed:
    series: Dict[str, Any]
    column: int
    y: float  # centre of the box, relative to the section's top
    children: List[Tuple[int, "Placed"]] = field(default_factory=list)  # (slot fed, child)


def feed_keys(kind: Optional[str]) -> Tuple[str, str]:
    """(series link, slot field) that moves a team on: losers move on in relegation brackets."""
    return ("loser_to", "loser_slot") if kind == KIND_RELEGATION else ("winner_to", "winner_slot")


def advancing_team(series: Dict[str, Any], kind: Optional[str]) -> Optional[str]:
    """The team that goes through from a decided series (its loser in a relegation bracket)."""
    return series.get("loser_id") if kind == KIND_RELEGATION else series.get("winner_id")


def layout_sections(series: List[Dict[str, Any]], kind: Optional[str]) -> List[Tuple[Placed, float]]:
    """One tree per final series, each as (root, section height). Leaves are stacked top to bottom in bracket order.

    A series is centred between the series that feed it; one fed from a single series (the other team had a bye)
    sits level with it, so the line stays straight.
    """
    link, slot_key = feed_keys(kind)
    by_id = {s.get("id"): s for s in series}
    first_round = min((s.get("round") or 1) for s in series) if series else 1
    children: Dict[Any, List[Tuple[int, Dict[str, Any]]]] = {}
    for item in series:
        target = item.get(link)
        if target in by_id:
            children.setdefault(target, []).append((item.get(slot_key) or 1, item))
    order = lambda s: ((s.get("round") or 0), (s.get("position") or 0))  # noqa: E731
    roots = sorted((s for s in series if s.get(link) not in by_id), key=order)

    sections = []
    for root in roots:
        cursor = [0.0]

        def place(item: Dict[str, Any]) -> Placed:
            node = Placed(item, (item.get("round") or 1) - first_round, 0.0)
            feeders = sorted(children.get(item.get("id"), []), key=lambda pair: pair[0])
            if not feeders:
                node.y = cursor[0] + BOX_HEIGHT / 2
                cursor[0] += BOX_HEIGHT + BOX_GAP
                return node
            node.children = [(slot, place(child)) for slot, child in feeders]
            ys = [child.y for _, child in node.children]
            node.y = sum(ys) / len(ys)
            return node

        tree = place(root)
        sections.append((tree, cursor[0] - BOX_GAP))
    return sections


# ------------------------------------------------------------------------------------- drawing
def _team(teams: Dict[str, Dict[str, Any]], team_id: Optional[str]) -> Dict[str, Any]:
    return teams.get(str(team_id)) or teams.get(team_id) or {} if team_id else {}


def _draw_row(image, draw, x: int, y: int, team_id, seed, wins, state: str, score_text: Optional[str],
              teams, accent, width: int = BOX_WIDTH, height: int = ROW_HEIGHT, show_seed: bool = True) -> None:
    """One team line. ``state`` is 'through', 'out' or 'open' (series undecided or no team yet)."""
    middle = y + height // 2
    if state == "through":
        draw.rectangle((x, y + 6, x + MARK_WIDTH - 1, y + height - 7), fill=accent)
    team = _team(teams, team_id)
    name_color = TEXT if state != "out" else FAINT
    name_font = font(13, bold=state == "through")

    cursor = x + MARK_WIDTH + 4
    if seed is not None:
        draw.text((cursor + SEED_WIDTH - 8, middle), str(seed), font=font(11), fill=FAINT, anchor="rm")
    cursor += SEED_WIDTH if show_seed else 8
    if team_id:
        canvas.draw_logo(image, draw, (cursor + TEAM_LOGO_SIZE // 2, middle), TEAM_LOGO_SIZE,
                         [team.get("code"), team.get("name")], team.get("name"))
        cursor += TEAM_LOGO_SIZE + 8
        label = team.get("name") or team.get("code") or "Unknown"
    else:
        label = "TBD"
        name_color = FAINT
        name_font = font(13)
    right = x + width - (SCORE_WIDTH if score_text is not None else 8)
    draw.text((cursor, middle), canvas.fit_text(label, name_font, right - cursor - 6), font=name_font, fill=name_color, anchor="lm")
    if score_text is not None:
        score_color = TEXT if state == "through" else FAINT if state == "out" else MUTED
        draw.text((x + width - 12, middle), score_text, font=font(13, bold=state == "through"), fill=score_color, anchor="rm")


def _draw_series(image, draw, x: int, top: int, series: Dict[str, Any], kind, teams, accent) -> None:
    draw.rounded_rectangle((x, top, x + BOX_WIDTH, top + BOX_HEIGHT), radius=6, fill=ROW_ALT)
    draw.line((x + 8, top + ROW_HEIGHT, x + BOX_WIDTH - 8, top + ROW_HEIGHT), fill=HAIRLINE, width=1)
    through = advancing_team(series, kind)
    decided = bool(series.get("winner_id"))
    forced = series.get("forced_winner_id")
    both = series.get("team1_id") and series.get("team2_id")
    for index, slot in enumerate(("1", "2")):
        team_id = series.get(f"team{slot}_id")
        wins = int(series.get(f"team{slot}_wins") or 0)
        state = "open"
        if decided and team_id:
            state = "through" if team_id == through else "out"
        if not both:
            score = None
        elif forced and decided:
            score = "W" if team_id == series.get("winner_id") else "FF"
        elif decided or wins or series.get(f"team{'2' if slot == '1' else '1'}_wins"):
            score = str(wins)
        else:
            score = "–"
        _draw_row(image, draw, x, top + index * ROW_HEIGHT, team_id, series.get(f"team{slot}_seed"), wins,
                  state, score, teams, accent)


def _draw_result(image, draw, x: int, y_center: float, team_id, teams, accent) -> None:
    top = int(y_center - RESULT_HEIGHT / 2)
    draw.rounded_rectangle((x, top, x + RESULT_WIDTH, top + RESULT_HEIGHT), radius=6,
                           fill=ROW_ALT if team_id else None, outline=None if team_id else HAIRLINE)
    if team_id:
        _draw_row(image, draw, x, top, team_id, None, 0, "through", None, teams, accent, RESULT_WIDTH, RESULT_HEIGHT,
                  show_seed=False)
    else:
        draw.text((x + RESULT_WIDTH // 2, top + RESULT_HEIGHT // 2), "—", font=font(13), fill=FAINT, anchor="mm")


def _connector(draw, start: Tuple[float, float], end: Tuple[float, float], lit: bool) -> None:
    """Elbow line from the right edge of one box to a slot on the left edge of the next."""
    (x0, y0), (x1, y1) = start, end
    middle = x0 + (x1 - x0) / 2
    color = MUTED if lit else DIVIDER
    draw.line([(x0, y0), (middle, y0), (middle, y1), (x1, y1)], fill=color, width=1, joint="curve")


def generate_bracket_image(
    bracket: Dict[str, Any],
    teams: Dict[str, Dict[str, Any]],
    title: str,
    subtitle: Optional[str] = None,
    accent_color: Optional[str] = None,
    result_label: Optional[str] = None,
    logo_names: Optional[List[Optional[str]]] = None,
) -> Optional[io.BytesIO]:
    """Render a bracket as a PNG, or None when it has no series.

    ``teams`` maps a team id to ``{"name", "code"}``. ``result_label`` heads the last column (defaults by kind,
    e.g. "Champion"). ``logo_names`` are tried for a division logo in the header.
    """
    series = bracket.get("series") or []
    if not series:
        return None
    try:
        rgb = resolve_accent(accent_color)
        key = bracket_cache.key({
            "kind": bracket.get("kind"), "series": series, "title": title, "subtitle": subtitle, "accent": rgb,
            "label": result_label, "logo": logo_names,
            "teams": {str(k): [v.get("name"), v.get("code")] for k, v in teams.items()},
        })
        cached = bracket_cache.get(key)
        if cached is not None:
            return cached
        image = _render(bracket, teams, title, subtitle, rgb, result_label, logo_names or [])
        return bracket_cache.put(key, image)
    except Exception as exc:
        logger.error("Failed to generate bracket image: %s", exc, exc_info=True)
        return None


def _render(bracket, teams, title, subtitle, accent, result_label, logo_names):
    kind = bracket.get("kind")
    series = bracket.get("series") or []
    sections = layout_sections(series, kind)
    columns = max(node.column for node in _walk([root for root, _ in sections])) + 1
    labelled = len(sections) > 1 and kind != KIND_PROMO_REL

    tree_width = columns * BOX_WIDTH + (columns - 1) * COLUMN_GAP
    content_width = max(tree_width + COLUMN_GAP + RESULT_WIDTH, MIN_CONTENT_WIDTH)
    body_height = int(sum(height for _, height in sections)) + SECTION_GAP * (len(sections) - 1)
    if labelled:
        body_height += SECTION_LABEL_HEIGHT * len(sections)

    top = canvas.PADDING + canvas.ACCENT_HEIGHT
    width = canvas.PADDING * 2 + INNER_PAD * 2 + content_width
    height = top + HEADER_HEIGHT + ROUND_LABEL_HEIGHT + body_height + BOTTOM_PAD + canvas.PADDING
    image, draw = canvas.new_image(width, height, accent)
    left = canvas.PADDING + INNER_PAD

    # Header, with the bracket's status on the right
    status = "Complete" if bracket.get("status") == "completed" else "In progress"
    status_font = font(12)
    status_width = int(status_font.getlength(status))
    header_middle = top + HEADER_HEIGHT // 2
    canvas.draw_header(image, draw, left, header_middle, content_width - status_width - 16, title, subtitle, logo_names)
    draw.text((left + content_width, header_middle), status, font=status_font, fill=FAINT, anchor="rm")

    # Round names (with best-of when a round plays more than one game)
    first_round = min((s.get("round") or 1) for s in series)
    last_round = max((s.get("round") or 1) for s in series)
    label_y = top + HEADER_HEIGHT + ROUND_LABEL_HEIGHT // 2 - 4
    small = font(11, bold=True)
    for column in range(columns):
        number = first_round + column
        name = round_name(number, last_round, kind)
        best_of = max((s.get("best_of") or 1) for s in series if (s.get("round") or 1) == number) if any(
            (s.get("round") or 1) == number for s in series) else 1
        text = f"{name.upper()}  ·  BO{best_of}" if best_of > 1 else name.upper()
        draw.text((left + column * (BOX_WIDTH + COLUMN_GAP), label_y), text, font=small, fill=FAINT, anchor="lm")
    result_x = left + tree_width + COLUMN_GAP
    draw.text((result_x, label_y), (result_label or RESULT_LABELS.get(kind, "Result")).upper(), font=small, fill=FAINT, anchor="lm")

    # Trees
    y = top + HEADER_HEIGHT + ROUND_LABEL_HEIGHT
    for index, (root, section_height) in enumerate(sections):
        if labelled:
            draw.text((left, y + SECTION_LABEL_HEIGHT // 2 - 2), f"Spot {index + 1}", font=font(12), fill=MUTED, anchor="lm")
            y += SECTION_LABEL_HEIGHT
        for node in _walk([root]):
            x = left + node.column * (BOX_WIDTH + COLUMN_GAP)
            box_top = int(y + node.y - BOX_HEIGHT / 2)
            for slot, child in node.children:
                child_x = left + child.column * (BOX_WIDTH + COLUMN_GAP) + BOX_WIDTH
                lit = bool(advancing_team(child.series, kind))
                _connector(draw, (child_x, y + child.y), (x, box_top + (slot - 1) * ROW_HEIGHT + ROW_HEIGHT / 2), lit)
            _draw_series(image, draw, x, box_top, node.series, kind, teams, accent)
        root_right = left + root.column * (BOX_WIDTH + COLUMN_GAP) + BOX_WIDTH
        outcome = advancing_team(root.series, kind)
        _connector(draw, (root_right, y + root.y), (result_x, y + root.y), bool(outcome))
        _draw_result(image, draw, result_x, y + root.y, outcome, teams, accent)
        y += section_height + SECTION_GAP
    return image


def _walk(nodes: List[Placed]):
    for node in nodes:
        yield node
        yield from _walk([child for _, child in node.children])
