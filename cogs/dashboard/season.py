"""Dashboard: the current season and past seasons."""

from __future__ import annotations

from typing import Any, List

import discord

from db.league_repository import SEASON_PHASE_LABELS
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage, FormModal, division_label

PAST_PER_PAGE = 10


def phase_label(season: dict) -> str:
    return SEASON_PHASE_LABELS.get(season.get("phase"), (season.get("phase") or "").title())


def season_line(season: dict) -> str:
    marker = "🟢" if season.get("active") else "⚪"
    champion = ""
    if season.get("champion_team_id"):
        team = TeamPlayerCache.get_team(season["champion_team_id"]) or {}
        champion = f" · 🏆 {team.get('name', 'Unknown')}"
    return f"{marker} **{season.get('name')}** (#{season.get('number')}) · {phase_label(season)}{champion}"


class SeasonPage(DashboardPage):
    title = "Season"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.page = 0

    async def load(self) -> None:
        self.season = LeagueCache.active_season()
        self.seasons: List[dict] = LeagueCache.seasons()
        if self.season:
            await LeagueCache.load_season_teams(self.season["id"])

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="📅 Season", color=EMBED_COLOR)
        if not self.season:
            embed.description = (
                "No season yet.\n\n**Create** the first season. It starts in **setup**: put teams in divisions, "
                "seed draft picks, then start the regular season."
            )
            return embed

        season = self.season
        embed.description = f"**{season.get('name')}** (#{season.get('number')}) · {phase_label(season)}"
        lines = []
        for division in LeagueCache.divisions():
            count = len(LeagueCache.team_ids_in_division(division["id"], season["id"]))
            lines.append(f"{division_label(division)} · {count} teams")
        assigned = set(LeagueCache.assigned_team_ids(season["id"]))
        missing = [t for t in TeamPlayerCache.get_all_teams() if t.get("id") not in assigned]
        if missing:
            lines.append(f"⚠️ {len(missing)} team(s) without a division")
        if lines:
            embed.add_field(name="Divisions this season", value="\n".join(lines), inline=False)

        past = [s for s in self.seasons if not s.get("active")]
        if past:
            pages = max(1, -(-len(past) // PAST_PER_PAGE))
            self.page = min(self.page, pages - 1)
            chunk = past[self.page * PAST_PER_PAGE:(self.page + 1) * PAST_PER_PAGE]
            embed.add_field(name="Past seasons", value="\n".join(season_line(s) for s in chunk), inline=False)
            if pages > 1:
                embed.set_footer(text=f"Past seasons page {self.page + 1}/{pages}")
        return embed

    def build(self) -> None:
        if not self.season:
            self.button("Create season", self._create, style=discord.ButtonStyle.success, emoji="➕", row=0)
            return
        self.button("Rename", self._rename, emoji="✏️", row=2)
        past = [s for s in self.seasons if not s.get("active")]
        if len(past) > PAST_PER_PAGE:
            self.button("◀", self._prev, row=3, disabled=self.page == 0)
            self.button("▶", self._next, row=3, disabled=(self.page + 1) * PAST_PER_PAGE >= len(past))

    async def _create(self, interaction: discord.Interaction) -> None:
        modal = FormModal("Create first season", self._submit_create)
        modal.season_name = modal.text("Season name", default="Season 1", max_length=100)
        await interaction.response.send_modal(modal)

    async def _submit_create(self, interaction: discord.Interaction, modal: FormModal) -> None:
        name = modal.season_name.value.strip()
        await self.apply(
            interaction,
            lambda: self.repo.create_first_season(name),
            f"**{name}** created in setup. Put every team in a division under **Teams**.",
        )

    async def _rename(self, interaction: discord.Interaction) -> None:
        modal = FormModal("Rename season", self._submit_rename)
        modal.season_name = modal.text("Season name", default=self.season.get("name"), max_length=100)
        await interaction.response.send_modal(modal)

    async def _submit_rename(self, interaction: discord.Interaction, modal: FormModal) -> None:
        name = modal.season_name.value.strip()
        await self.apply(interaction, lambda: self.repo.rename_season(self.season["id"], name), "Season renamed.")

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.show(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self.show(interaction)


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
