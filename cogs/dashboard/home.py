"""Dashboard home: the league at a glance and navigation to every section."""

from __future__ import annotations

from typing import Any

import discord

from db.league_repository import SEASON_PHASE_LABELS
from services.division_settings import DivisionSettings, describe_rules
from utils.branding import get_league_name
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage, division_label, refresh_caches


class HomePage(DashboardPage):
    title = "Dashboard"

    async def load(self) -> None:
        self.season = LeagueCache.active_season()
        self.divisions = LeagueCache.divisions()
        self.teams = TeamPlayerCache.get_all_teams()

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"📋 {get_league_name()} Dashboard", color=EMBED_COLOR)
        if self.season:
            phase = SEASON_PHASE_LABELS.get(self.season.get("phase"), self.season.get("phase") or "")
            embed.description = f"**{self.season.get('name')}** · {phase}"
        else:
            embed.description = "No season yet."

        season_id = (self.season or {}).get("id")
        for division in self.divisions[:20]:
            settings = DivisionSettings.from_division(division)
            count = len(LeagueCache.team_ids_in_division(division["id"], season_id)) if season_id else 0
            embed.add_field(
                name=f"Tier {division['tier']} · {division_label(division)}",
                value=f"{count} teams · {describe_rules(settings)}",
                inline=False,
            )

        steps = self._next_steps()
        if steps:
            embed.add_field(name="Next steps", value="\n".join(f"• {s}" for s in steps), inline=False)
        embed.set_footer(text=f"{len(self.divisions)} divisions · {len(self.teams)} teams")
        return embed

    def _next_steps(self) -> list:
        steps = []
        if not self.divisions:
            steps.append("Add your divisions under **Divisions** (tier 1 is the top).")
        if not self.teams:
            steps.append("Create teams under **Teams**.")
        if not self.season:
            steps.append("Create the first season under **Season**.")
        elif self.teams:
            assigned = set(LeagueCache.assigned_team_ids(self.season["id"]))
            missing = [t for t in self.teams if t.get("id") not in assigned]
            if missing and self.season.get("phase") == "setup":
                steps.append(f"{len(missing)} team(s) still need a division for this season (**Teams**).")
        return steps

    def build(self) -> None:
        from .divisions import DivisionsPage
        from .season import SeasonPage
        from .settings import SettingsPage
        from .teams import TeamsPage
        from .transactions import TransactionsPage

        self.button("Season", self._nav(SeasonPage), emoji="📅", style=discord.ButtonStyle.primary, row=0)
        self.button("Divisions", self._nav(DivisionsPage), emoji="🏟️", style=discord.ButtonStyle.primary, row=0)
        self.button("Teams", self._nav(TeamsPage), emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
        self.button("Transactions", self._nav(TransactionsPage), emoji="💼", row=1)
        self.button("Settings", self._nav(SettingsPage), emoji="⚙️", row=1)
        self.button("Refresh", self._refresh, emoji="🔄", row=3)

    def _nav(self, page_class: Any):
        async def callback(interaction: discord.Interaction) -> None:
            await self.open(interaction, page_class(self.session))
        return callback

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await refresh_caches(rosters=True)
        self.notice = "🔄 Reloaded from the database."
        await self.show(interaction)


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
