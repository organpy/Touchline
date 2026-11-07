"""Dashboard: recent games, and voiding a game reported by mistake.

Voiding keeps the game for the record but stops it counting. Standings recalculate and playoff
series recount. A playoff game can't be voided once a later series in its bracket has results
(void those first), and results lock when their part of the season ends.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from services.reporting import STAGE_LABELS, void_match
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage

RECENT_LIMIT = 25


def _name(team_id: Optional[str]) -> str:
    team = TeamPlayerCache.get_team(team_id) or {}
    return team.get("name") or team.get("code") or "?"


def match_label(match: Dict[str, Any]) -> str:
    return f"{_name(match.get('team1_id'))} {match.get('team1_score', 0)}-{match.get('team2_score', 0)} {_name(match.get('team2_id'))}"


class MatchesPage(DashboardPage):
    title = "Matches"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.selected: Optional[str] = None
        self.confirm = False

    async def load(self) -> None:
        self.season = LeagueCache.active_season()
        self.matches: List[Dict[str, Any]] = (
            await self.repo.list_recent_matches(self.season["id"], RECENT_LIMIT) if self.season else []
        )

    def _match(self) -> Optional[Dict[str, Any]]:
        return next((m for m in self.matches if m["id"] == self.selected), None)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🏀 Matches", color=EMBED_COLOR)
        if not self.matches:
            embed.description = "No games reported this season yet."
            return embed
        lines = []
        for match in self.matches[:15]:
            stage = STAGE_LABELS.get(match.get("stage"), match.get("stage"))
            line = f"{match_label(match)} · {stage}"
            lines.append(f"~~{line}~~ (void)" if match.get("status") == "void" else line)
        embed.description = "\n".join(lines)
        chosen = self._match()
        if chosen:
            played = (chosen.get("played_at") or "")[:16].replace("T", " ")
            embed.add_field(
                name="Selected game",
                value=f"{match_label(chosen)}\n{STAGE_LABELS.get(chosen.get('stage'), '')} · {played} UTC · `{chosen['id']}`",
                inline=False,
            )
            if self.confirm:
                embed.add_field(
                    name="⚠️ Void this game?",
                    value="It stops counting for standings and playoff series. This can't be undone; report it again if needed.",
                    inline=False,
                )
        embed.set_footer(text=f"Last {RECENT_LIMIT} games, newest first")
        return embed

    def build(self) -> None:
        voidable = [m for m in self.matches if m.get("status") == "final"]
        self.select(
            "Select a game...",
            [
                discord.SelectOption(
                    label=match_label(m)[:100],
                    value=m["id"],
                    description=STAGE_LABELS.get(m.get("stage"), m.get("stage") or "")[:100],
                    default=m["id"] == self.selected,
                )
                for m in voidable
            ],
            self._pick,
            row=0,
        )
        if self.confirm:
            self.button("Confirm void", self._void, style=discord.ButtonStyle.danger, row=1)
            self.button("Cancel", self._cancel, row=1)
        else:
            self.button("Void game", self._ask, style=discord.ButtonStyle.danger, emoji="🚫", row=1,
                        disabled=self._match() is None)

    async def _pick(self, interaction: discord.Interaction, match_id: str) -> None:
        self.selected = match_id
        self.confirm = False
        await self.show(interaction)

    async def _ask(self, interaction: discord.Interaction) -> None:
        self.confirm = True
        await self.show(interaction)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.confirm = False
        await self.show(interaction)

    async def _void(self, interaction: discord.Interaction) -> None:
        match = self._match()
        self.confirm = False
        self.selected = None
        if not match:
            await self.show(interaction)
            return
        await self.apply(
            interaction,
            lambda: void_match(self.session.supabase, match, interaction.user.id),
            f"Voided {match_label(match)}.",
        )


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
