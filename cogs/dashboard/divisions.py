"""Dashboard: divisions, their rules, groups, championship and promotion/relegation."""

from __future__ import annotations

from typing import Any, List, Optional

import discord

from db.league_repository import LeagueRuleError
from services.division_settings import (
    BOUNDARY_MODE_LABELS,
    MAX_BEST_OF,
    MAX_CHAMPIONSHIP_TEAMS,
    MAX_GAMES_PER_MATCHUP,
    MAX_POINTS_PER_WIN,
    MAX_SWAPS,
    SCORING_LABELS,
    SEEDING_LABELS,
    DivisionSettings,
    describe_boundary,
    describe_championship,
    describe_rules,
)
from utils.league_cache import LeagueCache

from .core import EMBED_COLOR, DashboardPage, FormModal, division_label, parse_color, parse_int


def _options(labels: dict, current: str) -> List[discord.SelectOption]:
    return [discord.SelectOption(label=label, value=value, default=value == current) for value, label in labels.items()]


def _on_off(current: bool, on: str = "On", off: str = "Off") -> List[discord.SelectOption]:
    return [
        discord.SelectOption(label=on, value="on", default=current),
        discord.SelectOption(label=off, value="off", default=not current),
    ]


def _embed_color(division: Optional[dict]) -> int:
    from utils.league_cache import hex_to_int

    return hex_to_int((division or {}).get("color")) or EMBED_COLOR


def parse_best_of_list(raw: str) -> List[int]:
    """``"1, 3, 3"`` → ``[1, 3, 3]`` (validated later by the settings)."""
    parts = [p for p in raw.replace("/", ",").replace(" ", ",").split(",") if p]
    if not parts:
        raise LeagueRuleError("INVALID", "Enter at least one best-of value, e.g. 1 or 1,3,5.")
    return [parse_int(p, "Best-of", minimum=1, maximum=MAX_BEST_OF) for p in parts]


def parse_pair(raw: str, label: str) -> tuple:
    """``"4 / 4"`` → ``(4, 4)``."""
    parts = [p for p in raw.replace(",", "/").replace(" ", "/").split("/") if p]
    if len(parts) != 2:
        raise LeagueRuleError("INVALID", f"{label}: enter two numbers like 4 / 4.")
    return tuple(parse_int(p, label, minimum=0, maximum=64) for p in parts)


class DivisionsPage(DashboardPage):
    title = "Divisions"

    async def load(self) -> None:
        self.divisions = LeagueCache.divisions()
        self.season = LeagueCache.active_season()

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🏟️ Divisions", color=EMBED_COLOR)
        if not self.divisions:
            embed.description = "No divisions yet. **Add division** to create the top tier (D1)."
            return embed
        lines = []
        for division in self.divisions:
            settings = DivisionSettings.from_division(division)
            teams = len(LeagueCache.team_ids_in_division(division["id"], (self.season or {}).get("id"))) if self.season else 0
            lines.append(
                f"**Tier {division['tier']} · {division_label(division)}** · {teams} teams\n"
                f"└ {describe_rules(settings)}"
            )
        embed.description = "\n".join(lines)
        embed.set_footer(text="Tier 1 is the top division. Pick a division to edit its rules.")
        return embed

    def build(self) -> None:
        self.select(
            "Edit a division...",
            [
                discord.SelectOption(label=division_label(d)[:100], value=d["id"], description=f"Tier {d['tier']}")
                for d in self.divisions
            ],
            self._pick,
            row=0,
        )
        self.button("Add division", self._add, style=discord.ButtonStyle.success, emoji="➕", row=1)

    async def _pick(self, interaction: discord.Interaction, division_id: str) -> None:
        await self.open(interaction, DivisionPage(self.session, division_id))

    async def _add(self, interaction: discord.Interaction) -> None:
        tier = len(self.divisions) + 1
        modal = FormModal("Add division", self._submit_add)
        modal.code = modal.text("Code", default=f"D{tier}", placeholder="D1, D2, PREM...", max_length=10)
        modal.name = modal.text("Name", placeholder="e.g. Premier Division", max_length=60)
        modal.color = modal.text("Colour (hex, optional)", placeholder="#1F8B4C", required=False, max_length=7)
        modal.emoji = modal.text("Emoji (optional)", required=False, max_length=60)
        await interaction.response.send_modal(modal)

    async def _submit_add(self, interaction: discord.Interaction, modal: FormModal) -> None:
        async def change() -> None:
            await self.repo.create_division(
                modal.code.value,
                modal.name.value,
                color=parse_color(modal.color.value),
                emoji=(modal.emoji.value or "").strip() or None,
            )

        await self.apply(interaction, change, f"Division **{modal.name.value.strip()}** added at the bottom tier.")


class DivisionPage(DashboardPage):
    title = "Division"

    def __init__(self, session: Any, division_id: str) -> None:
        super().__init__(session)
        self.division_id = division_id
        self.confirm_archive = False

    async def load(self) -> None:
        self.division = LeagueCache.division(self.division_id)
        self.settings = DivisionSettings.from_division(self.division)
        self.groups = LeagueCache.groups(self.division_id)
        self.below = LeagueCache.division_below(self.division_id)
        self.above = LeagueCache.division_above(self.division_id)
        self.season = LeagueCache.active_season()
        season_id = (self.season or {}).get("id")
        self.team_count = len(LeagueCache.team_ids_in_division(self.division_id, season_id)) if season_id else 0

    def embed(self) -> discord.Embed:
        if not self.division:
            return discord.Embed(title="Division not found", description="It may have been archived.", color=EMBED_COLOR)
        d = self.division
        embed = discord.Embed(title=f"{division_label(d)}", color=_embed_color(d))
        embed.description = (
            f"Tier **{d['tier']}** · Code `{(d.get('code') or '').upper()}` · "
            f"Colour `{d.get('color') or 'default'}` · {self.team_count} teams this season"
        )
        embed.add_field(name="Rules", value=describe_rules(self.settings), inline=False)
        groups = ", ".join(g["name"] for g in self.groups) or "None (one table)"
        embed.add_field(name="Groups", value=groups, inline=False)
        if d["tier"] == 1:
            embed.add_field(name="Championship playoff", value=describe_championship(self.settings.championship), inline=False)
        if self.below:
            embed.add_field(
                name=f"Promotion/relegation with {division_label(self.below)}",
                value=describe_boundary(self.settings.boundary_below),
                inline=False,
            )
        if self.above:
            above_boundary = DivisionSettings.from_division(self.above).boundary_below
            embed.add_field(
                name=f"Promotion to {division_label(self.above)}",
                value=f"{describe_boundary(above_boundary)}\n*(set on {self.above.get('code')})*",
                inline=False,
            )
        if self.confirm_archive:
            embed.add_field(name="⚠️ Archive?", value="Press **Confirm archive** to archive this division.", inline=False)
        return embed

    def build(self) -> None:
        if not self.division:
            return
        self.button("Edit info", self._edit_info, emoji="✏️", row=0)
        self.button("Rules", self._edit_rules, emoji="📏", row=0)
        self.button("Groups", self._groups, emoji="🗂️", row=0)
        self.button("Championship", self._edit_championship, emoji="🏆", row=1, disabled=self.division["tier"] != 1)
        self.button("Pro/rel below", self._edit_boundary, emoji="↕️", row=1, disabled=self.below is None)
        self.button("Move up", self._move_up, emoji="⬆️", row=2, disabled=self.above is None)
        self.button("Move down", self._move_down, emoji="⬇️", row=2, disabled=self.below is None)
        if self.confirm_archive:
            self.button("Confirm archive", self._archive, style=discord.ButtonStyle.danger, row=3)
            self.button("Cancel", self._cancel_archive, row=3)
        else:
            self.button("Archive", self._ask_archive, style=discord.ButtonStyle.danger, emoji="🗄️", row=3)

    # ------------------------------------------------------------------ info
    async def _edit_info(self, interaction: discord.Interaction) -> None:
        d = self.division
        modal = FormModal(f"Edit {d.get('code')}", self._submit_info)
        modal.code = modal.text("Code", default=d.get("code"), max_length=10)
        modal.name = modal.text("Name", default=d.get("name"), max_length=60)
        modal.color = modal.text("Colour (hex)", default=d.get("color"), required=False, max_length=7)
        modal.emoji = modal.text("Emoji", default=d.get("emoji"), required=False, max_length=60)
        await interaction.response.send_modal(modal)

    async def _submit_info(self, interaction: discord.Interaction, modal: FormModal) -> None:
        async def change() -> None:
            await self.repo.update_division(
                self.division_id,
                code=modal.code.value,
                name=modal.name.value.strip(),
                color=parse_color(modal.color.value),
                emoji=(modal.emoji.value or "").strip() or None,
            )

        await self.apply(interaction, change, "Division updated.")

    # ------------------------------------------------------------------ rules
    async def _edit_rules(self, interaction: discord.Interaction) -> None:
        s = self.settings
        modal = FormModal("Division rules", self._submit_rules)
        modal.scoring = modal.choice("Standings scoring", _options(SCORING_LABELS, s.scoring_mode))
        modal.points = modal.text("Points per win (points-per-win scoring)", default=str(s.points_per_win), max_length=2)
        modal.games = modal.text("Games per matchup", default=str(s.games_per_matchup), max_length=2)
        modal.picks = modal.choice("Draft picks", _on_off(s.picks_enabled, "Draft on", "No draft"))
        await interaction.response.send_modal(modal)

    async def _submit_rules(self, interaction: discord.Interaction, modal: FormModal) -> None:
        async def change() -> None:
            settings = DivisionSettings.from_division(LeagueCache.division(self.division_id))
            settings.scoring_mode = modal.scoring.values[0]
            settings.points_per_win = parse_int(modal.points.value, "Points per win", minimum=1, maximum=MAX_POINTS_PER_WIN)
            settings.games_per_matchup = parse_int(modal.games.value, "Games per matchup", minimum=1, maximum=MAX_GAMES_PER_MATCHUP)
            settings.picks_enabled = modal.picks.values[0] == "on"
            await self._save(settings)

        await self.apply(interaction, change, "Rules saved.")

    async def _save(self, settings: DivisionSettings) -> None:
        errors = settings.validate()
        if errors:
            raise LeagueRuleError("INVALID", " ".join(errors))
        await self.repo.update_division_settings(self.division_id, settings.to_dict())

    # ------------------------------------------------------------------ championship
    async def _edit_championship(self, interaction: discord.Interaction) -> None:
        c = self.settings.championship
        modal = FormModal("Championship playoff", self._submit_championship)
        modal.enabled = modal.choice("Championship playoff", _on_off(c.enabled))
        modal.teams = modal.text(f"Teams (2-{MAX_CHAMPIONSHIP_TEAMS}, top seeds get byes)", default=str(c.teams), max_length=2)
        modal.seeding = modal.choice("Seeding", _options(SEEDING_LABELS, c.seeding))
        modal.best_of = modal.text(
            "Best-of per round (first round first)",
            default=", ".join(str(v) for v in c.best_of_by_round),
            placeholder="1, 3, 5 (the last value repeats)",
            max_length=40,
        )
        await interaction.response.send_modal(modal)

    async def _submit_championship(self, interaction: discord.Interaction, modal: FormModal) -> None:
        async def change() -> None:
            settings = DivisionSettings.from_division(LeagueCache.division(self.division_id))
            c = settings.championship
            c.enabled = modal.enabled.values[0] == "on"
            c.teams = parse_int(modal.teams.value, "Championship teams", minimum=2, maximum=MAX_CHAMPIONSHIP_TEAMS)
            c.seeding = modal.seeding.values[0]
            c.best_of_by_round = parse_best_of_list(modal.best_of.value)
            if c.seeding == "by_group" and len(self.groups) < 2:
                raise LeagueRuleError("INVALID", "Seeding by group needs at least two groups in this division.")
            await self._save(settings)

        await self.apply(interaction, change, "Championship playoff saved.")

    # ------------------------------------------------------------------ boundary
    async def _edit_boundary(self, interaction: discord.Interaction) -> None:
        b = self.settings.boundary_below
        modal = FormModal(f"Pro/rel with {self.below.get('code')}", self._submit_boundary)
        modal.direct = modal.text(f"Automatic swaps (0-{MAX_SWAPS})", default=str(b.direct_swaps), max_length=1)
        modal.playoff = modal.text(f"Extra spots decided by playoffs (0-{MAX_SWAPS})", default=str(b.playoff_swaps), max_length=1)
        modal.mode = modal.choice(
            "Playoff format",
            _options(BOUNDARY_MODE_LABELS, b.mode),
            description="Separate: a relegation bracket here and a promotion bracket below. Cross: head-to-head.",
        )
        modal.sizes = modal.text(
            f"Playoff teams: relegation ({self.division.get('code')}) / promotion ({self.below.get('code')})",
            default=f"{b.upper_playoff_teams} / {b.lower_playoff_teams}",
            placeholder="4 / 4 (separate format only)",
            max_length=9,
        )
        modal.best_of = modal.text("Best-of per playoff series", default=str(b.best_of), max_length=1)
        await interaction.response.send_modal(modal)

    async def _submit_boundary(self, interaction: discord.Interaction, modal: FormModal) -> None:
        async def change() -> None:
            settings = DivisionSettings.from_division(LeagueCache.division(self.division_id))
            b = settings.boundary_below
            b.direct_swaps = parse_int(modal.direct.value, "Automatic swaps", minimum=0, maximum=MAX_SWAPS)
            b.playoff_swaps = parse_int(modal.playoff.value, "Playoff spots", minimum=0, maximum=MAX_SWAPS)
            b.mode = modal.mode.values[0]
            b.upper_playoff_teams, b.lower_playoff_teams = parse_pair(modal.sizes.value or "0 / 0", "Playoff teams")
            b.best_of = parse_int(modal.best_of.value, "Best-of", minimum=1, maximum=MAX_BEST_OF)
            b.normalized()
            await self._save(settings)

        await self.apply(interaction, change, "Promotion/relegation saved.")

    # ------------------------------------------------------------------ order and archive
    async def _move(self, interaction: discord.Interaction, step: int) -> None:
        order = [d["id"] for d in LeagueCache.divisions()]
        index = order.index(self.division_id)
        target = index + step
        order[index], order[target] = order[target], order[index]
        await self.apply(interaction, lambda: self.repo.reorder_divisions(order), "Tier order updated.")

    async def _move_up(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, -1)

    async def _move_down(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 1)

    async def _groups(self, interaction: discord.Interaction) -> None:
        await self.open(interaction, GroupsPage(self.session, self.division_id))

    async def _ask_archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = True
        await self.show(interaction)

    async def _cancel_archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = False
        await self.show(interaction)

    async def _archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = False
        season_id = (LeagueCache.active_season() or {}).get("id")
        name = self.division.get("name")
        parent = self.session.stack[-2] if len(self.session.stack) > 1 else self
        ok = await self.apply(
            interaction, lambda: self.repo.archive_division(self.division_id, season_id), f"**{name}** archived."
        )
        if ok and parent is not self:
            self.session.stack.pop()
            parent.notice = f"✅ **{name}** archived. Lower tiers moved up."
            await parent.show(interaction)


class GroupsPage(DashboardPage):
    """Optional groups (conferences) inside a division."""

    title = "Groups"

    def __init__(self, session: Any, division_id: str) -> None:
        super().__init__(session)
        self.division_id = division_id
        self.selected: Optional[str] = None

    async def load(self) -> None:
        self.division = LeagueCache.division(self.division_id)
        self.groups = LeagueCache.groups(self.division_id)
        if self.selected and not any(g["id"] == self.selected for g in self.groups):
            self.selected = None
        season_id = (LeagueCache.active_season() or {}).get("id")
        self.counts = {}
        for team_id in LeagueCache.team_ids_in_division(self.division_id, season_id) if season_id else []:
            group_id = (LeagueCache.assignment(team_id, season_id) or {}).get("group_id")
            self.counts[group_id] = self.counts.get(group_id, 0) + 1

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"🗂️ Groups · {division_label(self.division)}", color=_embed_color(self.division))
        if not self.groups:
            embed.description = (
                "No groups: the division plays as one table.\n"
                "Add groups (e.g. East and West) to split the standings and seed playoffs by group."
            )
        else:
            lines = [f"• **{g['name']}** · {self.counts.get(g['id'], 0)} teams" for g in self.groups]
            if self.counts.get(None):
                lines.append(f"• *No group* · {self.counts[None]} teams")
            embed.description = "\n".join(lines)
        embed.set_footer(text="Put teams in groups from Teams → a team → Group.")
        return embed

    def build(self) -> None:
        self.select(
            "Select a group...",
            [discord.SelectOption(label=g["name"][:100], value=g["id"], default=g["id"] == self.selected) for g in self.groups],
            self._pick,
            row=0,
        )
        self.button("Add group", self._add, style=discord.ButtonStyle.success, emoji="➕", row=1)
        self.button("Rename", self._rename, emoji="✏️", row=1, disabled=not self.selected)
        self.button("Delete", self._delete, style=discord.ButtonStyle.danger, emoji="🗑️", row=1, disabled=not self.selected)

    def _group(self) -> Optional[dict]:
        return next((g for g in self.groups if g["id"] == self.selected), None)

    async def _pick(self, interaction: discord.Interaction, group_id: str) -> None:
        self.selected = group_id
        await self.show(interaction)

    async def _add(self, interaction: discord.Interaction) -> None:
        modal = FormModal("Add group", self._submit_add)
        modal.name = modal.text("Group name", placeholder="East", max_length=40)
        await interaction.response.send_modal(modal)

    async def _submit_add(self, interaction: discord.Interaction, modal: FormModal) -> None:
        name = modal.name.value.strip()
        await self.apply(interaction, lambda: self.repo.create_group(self.division_id, name), f"Group **{name}** added.")

    async def _rename(self, interaction: discord.Interaction) -> None:
        group = self._group()
        modal = FormModal("Rename group", self._submit_rename)
        modal.name = modal.text("Group name", default=(group or {}).get("name"), max_length=40)
        await interaction.response.send_modal(modal)

    async def _submit_rename(self, interaction: discord.Interaction, modal: FormModal) -> None:
        name = modal.name.value.strip()
        await self.apply(interaction, lambda: self.repo.rename_group(self.selected, name), "Group renamed.")

    async def _delete(self, interaction: discord.Interaction) -> None:
        group = self._group() or {}
        await self.apply(
            interaction,
            lambda: self.repo.delete_group(self.selected),
            f"Group **{group.get('name')}** deleted; its teams now have no group.",
        )


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
