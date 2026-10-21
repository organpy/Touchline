"""Dashboard: the current season and past seasons."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import discord

from db.league_repository import SEASON_PHASE_LABELS
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

from services.standings import recompute_season
from services.league import (
    NEXT_STEP,
    Movement,
    default_plan_builder,
    end_regular_season,
    final_standings,
    finish_postseason,
    regular_season_readiness,
    season_outcome,
    start_next_season,
    start_regular_season,
    suggested_next_name,
)

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


MOVE_VERBS = {"promoted": "⬆️ promoted to", "relegated": "⬇️ relegated to"}


def describe_movement(movement: Movement) -> str:
    team = TeamPlayerCache.get_team(movement.team_id) or {}
    target = LeagueCache.division(movement.to_division_id)
    verb = MOVE_VERBS.get(movement.reason.split("_")[0], "moves to")
    via = " (playoff)" if "playoff" in movement.reason else ""
    return f"**{team.get('name', '?')}** {verb} {division_label(target)}{via}"


def describe_plan_bracket(bracket: Dict[str, Any]) -> str:
    kinds = {
        "championship": "Championship",
        "promotion": "Promotion playoff",
        "relegation": "Relegation playoff",
        "promo_rel": "Promotion/relegation playoff",
    }
    division = LeagueCache.division(bracket.get("division_id"))
    teams = len(bracket.get("entries") or [])
    return f"{kinds.get(bracket.get('kind'), bracket.get('kind'))} · {division_label(division)} · {teams} teams"


class SeasonPage(DashboardPage):
    title = "Season"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.page = 0
        self.preview: Optional[Dict[str, Any]] = None  # pending lifecycle step awaiting confirmation

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
        if self.preview:
            embed.add_field(name=f"➡️ {self.preview['title']}", value=self.preview["text"][:1024], inline=False)
        return embed

    def build(self) -> None:
        if not self.season:
            self.button("Create season", self._create, style=discord.ButtonStyle.success, emoji="➕", row=0)
            return
        phase = self.season.get("phase")
        if self.preview:
            self.button(
                f"Confirm: {self.preview['title']}", self._confirm, style=discord.ButtonStyle.success,
                row=0, disabled=bool(self.preview.get("blocked")),
            )
            self.button("Cancel", self._cancel, row=0)
        elif phase in NEXT_STEP:
            self.button(NEXT_STEP[phase], self._step, style=discord.ButtonStyle.primary, emoji="➡️", row=0)
        self.button("Rename", self._rename, emoji="✏️", row=2)
        if phase == "regular":
            self.button("Recalculate standings", self._recalculate, emoji="🔄", row=2)
        past = [s for s in self.seasons if not s.get("active")]
        if len(past) > PAST_PER_PAGE:
            self.button("◀", self._prev, row=3, disabled=self.page == 0)
            self.button("▶", self._next, row=3, disabled=(self.page + 1) * PAST_PER_PAGE >= len(past))

    # ------------------------------------------------------------------ lifecycle
    async def _step(self, interaction: discord.Interaction) -> None:
        phase = self.season.get("phase")
        if phase == "offseason":
            modal = FormModal("Start next season", self._submit_next)
            modal.season_name = modal.text("New season name", default=suggested_next_name(self.season), max_length=100)
            await interaction.response.send_modal(modal)
            return
        await interaction.response.defer()
        self.preview = await self._build_preview(phase)
        await self.show(interaction)

    async def _build_preview(self, phase: str) -> Dict[str, Any]:
        season_id = self.season["id"]
        if phase == "setup":
            team_ids = [t["id"] for t in TeamPlayerCache.get_all_teams()]
            await LeagueCache.load_season_teams(season_id)
            readiness = regular_season_readiness(season_id, team_ids)
            lines = [f"❌ {p}" for p in readiness.problems] + [f"⚠️ {w}" for w in readiness.warnings]
            if readiness.ok:
                lines.insert(0, "Games can be reported once it starts. Teams that haven't played can still change division.")
            return {"title": NEXT_STEP[phase], "text": "\n".join(lines), "blocked": not readiness.ok, "phase": phase}
        if phase == "regular":
            standings = await final_standings(self.repo, season_id)
            plan = default_plan_builder()(season_id, standings)
            lines = ["Standings lock for good."]
            brackets = plan.get("brackets") or []
            if brackets:
                lines.append(f"{len(brackets)} playoff bracket(s) will be created:")
                lines.extend(f"• {describe_plan_bracket(b)}" for b in brackets)
            else:
                lines.append("No playoffs are configured: the final standings decide the season.")
            return {"title": NEXT_STEP[phase], "text": "\n".join(lines), "phase": phase}
        if phase == "postseason":
            outcome = await season_outcome(self.repo, season_id)
            lines = []
            if outcome.unfinished_brackets:
                lines.append(f"❌ {outcome.unfinished_brackets} playoff bracket(s) are still being played.")
            champion = TeamPlayerCache.get_team(outcome.champion_team_id) if outcome.champion_team_id else None
            lines.append(f"🏆 Champion: **{(champion or {}).get('name', 'none')}**")
            lines.extend(f"• {describe_movement(m)}" for m in outcome.movements)
            if not outcome.movements:
                lines.append("No promotions or relegations.")
            return {"title": NEXT_STEP[phase], "text": "\n".join(lines), "blocked": bool(outcome.unfinished_brackets), "phase": phase}
        return {"title": "Nothing to do", "text": "", "blocked": True, "phase": phase}

    async def _confirm(self, interaction: discord.Interaction) -> None:
        preview, self.preview = self.preview, None
        season = self.season
        team_ids = [t["id"] for t in TeamPlayerCache.get_all_teams()]
        actions = {
            "setup": (lambda: start_regular_season(self.repo, season, team_ids), "Regular season started. Owners can report games now."),
            "regular": (lambda: end_regular_season(self.repo, season), "Regular season ended. Standings are locked."),
            "postseason": (lambda: finish_postseason(self.repo, season), "Season finished. Review the moves, then start the next season."),
        }
        change, success = actions[preview["phase"]]
        await self.apply(interaction, change, success, rosters=True)

    async def _recalculate(self, interaction: discord.Interaction) -> None:
        await self.apply(interaction, lambda: recompute_season(self.repo, self.season["id"]), "Standings recalculated.")

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.preview = None
        await self.show(interaction)

    async def _submit_next(self, interaction: discord.Interaction, modal: FormModal) -> None:
        name = modal.season_name.value.strip()
        await self.apply(
            interaction,
            lambda: start_next_season(self.repo, self.season, name),
            f"**{name}** started in setup. Teams moved divisions and rosters carried over.",
            rosters=True,
        )

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
