"""What the public league board shows, and which announcements are due.

Pure functions over league data. The board cog (``cogs/board/board.py``) fetches the data, draws
the standings images and posts or edits the messages.

Board posts, in order:

* ``overview``: season, phase, champion, each division's leader and the zone legend
* ``division:<id>``: one per division, a standings image coloured by zone (text table fallback)
* ``bracket:<id>``: during and after the postseason, one per bracket: its image (text when the image failed)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import discord

from cogs.dashboard.core import division_label
from cogs.dashboard.playoffs import bracket_title, render_bracket, team_name
from db.league_repository import SEASON_PHASE_LABELS
from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION
from services.movements import outcome_from_brackets
from services.standings import ZONE_EMOJI, ZONE_LABELS
from utils.league_cache import LeagueCache

BOARD_COLOR = 0x2B2D31
BRACKETS_PER_POST = 5
MAX_DIVISION_FIELDS = 24
PLAYOFF_PHASES = {"postseason", "offseason", "completed"}

MOVE_REASONS = {
    "promoted": "⬆️ promoted",
    "relegated": "⬇️ relegated",
    "promoted_playoff": "🔼 promoted via playoff",
    "relegated_playoff": "🔽 relegated via playoff",
}


@dataclass
class BoardData:
    league_name: str
    season: Optional[Dict[str, Any]]
    standings: List[Dict[str, Any]] = field(default_factory=list)
    brackets: List[Dict[str, Any]] = field(default_factory=list)
    movements: List[Dict[str, Any]] = field(default_factory=list)

    def by_division(self) -> Dict[str, List[Dict[str, Any]]]:
        """Standings grouped per division, divisions by tier, rows by rank."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.standings:
            grouped.setdefault(row.get("division_id"), []).append(row)
        ordered = sorted(grouped, key=lambda d: ((LeagueCache.division(d) or {}).get("tier") or 999, d or ""))
        return {d: sorted(grouped[d], key=lambda r: r.get("rank") or 999) for d in ordered}


@dataclass
class BoardPost:
    key: str
    embed: discord.Embed
    image: Optional[bytes] = None
    filename: Optional[str] = None

    def file(self) -> Optional[discord.File]:
        """A fresh attachment (a discord.File can only be sent once)."""
        if self.image is None:
            return None
        import io

        return discord.File(io.BytesIO(self.image), filename=self.filename)


def _color(division: Optional[Dict[str, Any]]) -> int:
    raw = (division or {}).get("color")
    try:
        return int(str(raw).lstrip("#"), 16) if raw else BOARD_COLOR
    except ValueError:
        return BOARD_COLOR


def zone_legend(rows: Iterable[Dict[str, Any]]) -> str:
    present = {row.get("zone") for row in rows}
    return " · ".join(f"{ZONE_EMOJI[z]} {ZONE_LABELS[z]}" for z in ZONE_LABELS if z in present)


def record(row: Dict[str, Any]) -> str:
    diff = row.get("point_diff") or 0
    return f"{row.get('wins', 0)}-{row.get('losses', 0)} ({diff:+d})"


def overview_embed(data: BoardData) -> discord.Embed:
    season = data.season
    embed = discord.Embed(title=f"🏟️ {data.league_name}", color=BOARD_COLOR, timestamp=datetime.now(timezone.utc))
    if not season:
        embed.description = "No season yet. An admin can create one in `/dashboard` → **Season**."
        return embed
    phase = SEASON_PHASE_LABELS.get(season.get("phase"), (season.get("phase") or "").title())
    lines = [f"**{season.get('name', 'Season')}** · {phase}"]
    if season.get("champion_team_id"):
        lines.append(f"🏆 Champion: **{team_name(season['champion_team_id'])}**")
    embed.description = "\n".join(lines)

    divisions = data.by_division()
    for index, (division_id, rows) in enumerate(divisions.items()):
        if index == MAX_DIVISION_FIELDS:
            embed.add_field(name="…", value=f"and {len(divisions) - index} more divisions", inline=False)
            break
        leader = rows[0] if rows else None
        value = f"1. **{leader.get('team_name') or team_name(leader.get('team_id'))}** {record(leader)}" if leader else "No teams"
        value += f"\n{len(rows)} teams"
        embed.add_field(name=division_label(LeagueCache.division(division_id)), value=value, inline=True)

    legend = zone_legend(data.standings)
    if legend:
        embed.add_field(name="Zones", value=legend, inline=False)
    embed.set_footer(text="Updates automatically after every result")
    return embed


def text_table(rows: List[Dict[str, Any]]) -> str:
    lines = ["```", "Rk Team           W  L    ±", "-" * 28]
    for row in rows:
        name = (row.get("team_name") or team_name(row.get("team_id")))[:12].ljust(12)
        lines.append(
            f"{row.get('rank', '?'):<2} {name} {row.get('wins', 0):>2} {row.get('losses', 0):>2} "
            f"{row.get('point_diff', 0) or 0:>+5d} {ZONE_EMOJI.get(row.get('zone'), '')}"
        )
    lines.append("```")
    return "\n".join(lines)[:4000]


def division_post(division_id: str, rows: List[Dict[str, Any]], season: Dict[str, Any], image: Optional[bytes]) -> BoardPost:
    division = LeagueCache.division(division_id) or {}
    embed = discord.Embed(title=f"{division_label(division)} · {season.get('name', '')}", color=_color(division))
    filename = None
    if image is not None:
        filename = f"standings-{(division.get('code') or 'division').lower()}.png"
        embed.set_image(url=f"attachment://{filename}")
    else:
        embed.description = text_table(rows)
    legend = zone_legend(rows)
    if legend:
        embed.set_footer(text=legend)
    return BoardPost(key=f"division:{division_id}", embed=embed, image=image, filename=filename)


def bracket_image_key(bracket: Dict[str, Any]) -> str:
    """Key of a bracket's image in the ``images`` passed to ``board_posts``."""
    return f"bracket:{bracket.get('id')}"


def shows_brackets(data: BoardData) -> bool:
    return bool(data.season and data.season.get("phase") in PLAYOFF_PHASES and data.brackets)


def bracket_posts(data: BoardData, images: Optional[Dict[str, bytes]] = None) -> List[BoardPost]:
    """One post per bracket: the bracket image, or the text bracket when there is no image."""
    if not shows_brackets(data):
        return []
    posts = []
    for bracket in data.brackets:
        key = bracket_image_key(bracket)
        image = (images or {}).get(key)
        division = LeagueCache.division(bracket.get("division_id"))
        embed = discord.Embed(title=bracket_title(bracket)[:256], color=_color(division))
        filename = None
        if image is not None:
            filename = f"bracket-{bracket.get('kind')}-{(division or {}).get('code') or 'division'}.png".lower()
            embed.set_image(url=f"attachment://{filename}")
        else:
            embed.description = render_bracket(bracket)[:4000]
        posts.append(BoardPost(key=key, embed=embed, image=image, filename=filename))
    return posts


def board_posts(data: BoardData, images: Optional[Dict[str, bytes]] = None) -> List[BoardPost]:
    """Every board message in display order. ``images`` maps division id → standings PNG and
    ``bracket:<id>`` → bracket PNG."""
    posts = [BoardPost(key="overview", embed=overview_embed(data))]
    if data.season:
        for division_id, rows in data.by_division().items():
            posts.append(division_post(division_id, rows, data.season, (images or {}).get(division_id)))
    posts.extend(bracket_posts(data, images))
    return posts


# ---------------------------------------------------------------------------------- announcements
def _bracket_result(bracket: Dict[str, Any]) -> str:
    kind = bracket.get("kind")
    division = LeagueCache.division(bracket.get("division_id"))
    champion, movements = outcome_from_brackets([bracket])
    if kind == KIND_CHAMPIONSHIP:
        return f"🏆 **{team_name(champion)}** win the {division_label(division)} championship!" if champion else "The championship is decided."
    lines = []
    for move in movements:
        target = division_label(LeagueCache.division(move.to_division_id))
        verb = "promoted to" if move.reason.startswith("promoted") else "relegated to"
        emoji = "⬆️" if verb.startswith("promoted") else "⬇️"
        lines.append(f"{emoji} **{team_name(move.team_id)}** {verb} {target}")
    if not lines and kind == KIND_PROMO_REL:
        lines.append(f"🛡️ {division_label(division)} holds on: nobody changes division.")
    if not lines and kind in (KIND_PROMOTION, KIND_RELEGATION):
        lines.append("Bracket finished.")
    return "\n".join(lines)


def postseason_embed(data: BoardData) -> discord.Embed:
    embed = discord.Embed(title=f"{data.season.get('name', 'Season')}: the playoffs are set", color=BOARD_COLOR)
    for bracket in data.brackets[:BRACKETS_PER_POST]:
        embed.add_field(name=bracket_title(bracket)[:256], value=render_bracket(bracket)[:1000], inline=False)
    if len(data.brackets) > BRACKETS_PER_POST:
        embed.set_footer(text=f"+{len(data.brackets) - BRACKETS_PER_POST} more brackets on the league board")
    return embed


def season_end_embed(data: BoardData) -> discord.Embed:
    season = data.season or {}
    embed = discord.Embed(title=f"🏁 {season.get('name', 'Season')} is complete", color=BOARD_COLOR)
    embed.description = f"🏆 Champion: **{team_name(season.get('champion_team_id'))}**"
    moves = []
    for move in data.movements:
        source = (LeagueCache.division(move.get("from_division_id")) or {}).get("code", "?")
        target = (LeagueCache.division(move.get("to_division_id")) or {}).get("code", "?")
        moves.append(f"{MOVE_REASONS.get(move.get('reason'), move.get('reason'))}: **{team_name(move.get('team_id'))}** {source} → {target}")
    if moves:
        embed.add_field(name="Promotion and relegation", value="\n".join(moves)[:1024], inline=False)
    return embed


def due_announcements(data: BoardData) -> List[tuple]:
    """Every announcement the current state deserves, as (key, embed). The caller skips sent keys."""
    season = data.season
    if not season:
        return []
    items = []
    if data.brackets and season.get("phase") in PLAYOFF_PHASES:
        items.append((f"postseason:{season['id']}", lambda: postseason_embed(data)))
    for bracket in data.brackets:
        if bracket.get("status") == "completed":
            items.append((
                f"bracket:{bracket['id']}",
                lambda b=bracket: discord.Embed(title=bracket_title(b)[:256], description=_bracket_result(b), color=BOARD_COLOR),
            ))
    if season.get("champion_team_id") and season.get("phase") in ("offseason", "completed"):
        items.append((f"champion:{season['id']}", lambda: season_end_embed(data)))
    return items


def pending_announcements(data: BoardData, announced: Set[str]) -> List[tuple]:
    """(key, embed) for announcements not sent yet, oldest first."""
    return [(key, build()) for key, build in due_announcements(data) if key not in announced]


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
