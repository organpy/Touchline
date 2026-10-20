"""Dashboard: playoff brackets (view, swap seeds before the first game, decide forfeits)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage, division_label

KIND_TITLES = {
    KIND_CHAMPIONSHIP: "🏆 Championship",
    KIND_PROMOTION: "🔼 Promotion playoff",
    KIND_RELEGATION: "🔽 Relegation playoff",
    KIND_PROMO_REL: "↕️ Promotion/relegation playoff",
}
CLEAR = "__clear"


def team_name(team_id: Optional[str]) -> str:
    if not team_id:
        return "TBD"
    team = TeamPlayerCache.get_team(team_id) or {}
    return team.get("name") or team.get("code") or "?"


def bracket_title(bracket: Dict[str, Any]) -> str:
    division = LeagueCache.division(bracket.get("division_id"))
    title = f"{KIND_TITLES.get(bracket.get('kind'), bracket.get('kind'))} · {division_label(division)}"
    if bracket.get("kind") == KIND_PROMO_REL:
        title += f" v {division_label(LeagueCache.division(bracket.get('lower_division_id')))}"
    return title


def round_name(round_number: int, last_round: int, kind: str) -> str:
    if kind == KIND_CHAMPIONSHIP:
        remaining = last_round - round_number
        return {0: "Final", 1: "Semifinals", 2: "Quarterfinals"}.get(remaining, f"Round {round_number}")
    return f"Round {round_number}"


def series_line(series: Dict[str, Any], seeds: Dict[str, int]) -> str:
    def side(team_id: Optional[str], wins: int) -> str:
        seed = f"({seeds[team_id]}) " if team_id in seeds else ""
        name = team_name(team_id)
        if series.get("winner_id") and team_id == series["winner_id"]:
            name = f"**{name}**"
        return f"{seed}{name}"

    t1, t2 = series.get("team1_id"), series.get("team2_id")
    score = f"{series.get('team1_wins', 0)}–{series.get('team2_wins', 0)}"
    status = {"completed": "✅", "ready": "🟢", "waiting": "⏳"}.get(series.get("status"), "")
    forced = " (forfeit)" if series.get("forced_winner_id") else ""
    best_of = f" · bo{series['best_of']}" if (series.get("best_of") or 1) > 1 else ""
    return f"{status} {side(t1, 0)} {score} {side(t2, 0)}{best_of}{forced}"


def render_bracket(bracket: Dict[str, Any]) -> str:
    """Text view of a bracket, round by round."""
    seeds = {e["team_id"]: e["seed"] for e in bracket.get("bracket_entries") or []}
    series = sorted(bracket.get("series") or [], key=lambda s: (s.get("round") or 0, s.get("position") or 0))
    if not series:
        return "No series."
    last_round = max(s.get("round") or 1 for s in series)
    lines: List[str] = []
    current = None
    for item in series:
        if item.get("round") != current:
            current = item.get("round")
            lines.append(f"__{round_name(current, last_round, bracket.get('kind'))}__")
        lines.append(series_line(item, seeds))
    return "\n".join(lines)


def bracket_started(bracket: Dict[str, Any]) -> bool:
    return any(
        (s.get("team1_wins") or 0) + (s.get("team2_wins") or 0) > 0 or s.get("forced_winner_id")
        for s in bracket.get("series") or []
    )


class PlayoffsPage(DashboardPage):
    title = "Playoffs"

    async def load(self) -> None:
        self.season = LeagueCache.active_season()
        self.brackets = await self.repo.list_brackets(self.season["id"]) if self.season else []

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🏆 Playoffs", color=EMBED_COLOR)
        if not self.brackets:
            embed.description = "No brackets yet. They are created when the regular season ends (**Season**)."
            return embed
        done = sum(1 for b in self.brackets if b.get("status") == "completed")
        embed.description = f"{done}/{len(self.brackets)} brackets finished. Pick one to manage it."
        for bracket in self.brackets[:8]:
            status = "✅ Finished" if bracket.get("status") == "completed" else "▶️ In progress"
            embed.add_field(name=f"{bracket_title(bracket)} · {status}", value=render_bracket(bracket)[:1024], inline=False)
        return embed

    def build(self) -> None:
        self.select(
            "Manage a bracket...",
            [
                discord.SelectOption(label=bracket_title(b)[:100], value=b["id"]) for b in self.brackets
            ],
            self._pick,
            row=0,
        )

    async def _pick(self, interaction: discord.Interaction, bracket_id: str) -> None:
        await self.open(interaction, BracketPage(self.session, bracket_id))


class BracketPage(DashboardPage):
    title = "Bracket"

    def __init__(self, session: Any, bracket_id: str) -> None:
        super().__init__(session)
        self.bracket_id = bracket_id
        self.series_id: Optional[str] = None

    async def load(self) -> None:
        season = LeagueCache.active_season()
        brackets = await self.repo.list_brackets(season["id"]) if season else []
        self.bracket = next((b for b in brackets if b["id"] == self.bracket_id), None)

    def _series(self) -> List[Dict[str, Any]]:
        return sorted((self.bracket or {}).get("series") or [], key=lambda s: (s.get("round") or 0, s.get("position") or 0))

    def embed(self) -> discord.Embed:
        if not self.bracket:
            return discord.Embed(title="Bracket not found", color=EMBED_COLOR)
        embed = discord.Embed(title=bracket_title(self.bracket), color=EMBED_COLOR, description=render_bracket(self.bracket)[:4000])
        if bracket_started(self.bracket):
            embed.set_footer(text="Games have been played, so seeds are fixed. Void games under Matches to change results.")
        else:
            embed.set_footer(text="No games yet: you can still swap seeds (manual tiebreak).")
        return embed

    def build(self) -> None:
        if not self.bracket:
            return
        entries = sorted(self.bracket.get("bracket_entries") or [], key=lambda e: e["seed"])
        if not bracket_started(self.bracket) and len(entries) >= 2:
            swap = discord.ui.Select(
                placeholder="Swap two seeds...",
                min_values=2,
                max_values=2,
                options=[
                    discord.SelectOption(label=f"({e['seed']}) {team_name(e['team_id'])}"[:100], value=e["team_id"])
                    for e in entries[:25]
                ],
                row=0,
            )
            swap.callback = lambda interaction: self._swap(interaction, swap.values)
            self.add_item(swap)

        open_series = [s for s in self._series() if s.get("team1_id") and s.get("team2_id")]
        self.select(
            "Decide a series by forfeit...",
            [
                discord.SelectOption(
                    label=f"R{s['round']}: {team_name(s['team1_id'])} v {team_name(s['team2_id'])}"[:100],
                    value=s["id"],
                    default=s["id"] == self.series_id,
                )
                for s in open_series
            ],
            self._pick_series,
            row=1,
        )
        chosen = next((s for s in open_series if s["id"] == self.series_id), None)
        if chosen:
            options = [
                discord.SelectOption(label=f"{team_name(chosen[k])} wins"[:100], value=chosen[k])
                for k in ("team1_id", "team2_id")
            ]
            if chosen.get("forced_winner_id"):
                options.append(discord.SelectOption(label="Clear forfeit (use game results)", value=CLEAR))
            self.select("Winner...", options, self._force, row=2)

    async def _swap(self, interaction: discord.Interaction, team_ids: List[str]) -> None:
        a, b = team_ids
        await self.apply(
            interaction,
            lambda: self.repo.swap_bracket_seeds(self.bracket_id, a, b),
            f"Swapped seeds of **{team_name(a)}** and **{team_name(b)}**.",
        )

    async def _pick_series(self, interaction: discord.Interaction, series_id: str) -> None:
        self.series_id = series_id
        await self.show(interaction)

    async def _force(self, interaction: discord.Interaction, value: str) -> None:
        series_id = self.series_id
        self.series_id = None
        if value == CLEAR:
            await self.apply(interaction, lambda: self.repo.force_series_winner(series_id, None), "Forfeit cleared.")
            return
        await self.apply(
            interaction,
            lambda: self.repo.force_series_winner(series_id, value),
            f"**{team_name(value)}** advances by forfeit.",
        )


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
