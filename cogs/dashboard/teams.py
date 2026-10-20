"""Dashboard: teams, their division for the season, owner and logo."""

from __future__ import annotations

import logging
from typing import Any

import discord

from db.league_repository import LeagueRuleError
from utils.league_cache import LeagueCache, hex_to_int
from utils.logo_store import LogoError, remove_cached_logo, upload_team_logo
from utils.team_picker import team_select_for
from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage, FormModal, division_label, parse_color

logger = logging.getLogger(__name__)

REMOVE = "__remove"
NO_GROUP = "__nogroup"


def team_title(team: dict) -> str:
    emoji = f"{team['emoji']} " if team.get("emoji") else ""
    return f"{emoji}{team.get('name')} ({(team.get('code') or '').upper()})"


class TeamsPage(DashboardPage):
    title = "Teams"

    async def load(self) -> None:
        self.teams = TeamPlayerCache.get_all_teams()
        self.season = LeagueCache.active_season()
        season_id = (self.season or {}).get("id")
        assigned = set(LeagueCache.assigned_team_ids(season_id)) if season_id else set()
        self.unassigned = [t for t in self.teams if t.get("id") not in assigned]

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="🛡️ Teams", color=EMBED_COLOR)
        if not self.teams:
            embed.description = "No teams yet. **Add team** to create one."
            return embed
        lines = []
        season_id = (self.season or {}).get("id")
        if season_id:
            for division in LeagueCache.divisions():
                count = len(LeagueCache.team_ids_in_division(division["id"], season_id))
                lines.append(f"**{division_label(division)}** · {count} teams")
        if self.unassigned:
            names = ", ".join(t.get("name", "?") for t in self.unassigned[:15])
            more = f" and {len(self.unassigned) - 15} more" if len(self.unassigned) > 15 else ""
            where = "this season" if self.season else "(create a season to assign divisions)"
            lines.append(f"\n⚠️ **No division {where}:** {names}{more}")
        embed.description = "\n".join(lines) or f"{len(self.teams)} teams."
        embed.set_footer(text=f"{len(self.teams)} teams · pick one to edit it")
        return embed

    def build(self) -> None:
        if self.teams:
            self.add_item(team_select_for(self.teams, on_pick=self._pick, placeholder="Edit a team...", row=0))
        self.button("Add team", self._add, style=discord.ButtonStyle.success, emoji="➕", row=1)

    async def _pick(self, interaction: discord.Interaction) -> None:
        await self.open(interaction, TeamPage(self.session, interaction.data["values"][0]))

    async def _add(self, interaction: discord.Interaction) -> None:
        modal = FormModal("Add team", self._submit_add)
        modal.name = modal.text("Team name", placeholder="e.g. Frost Falcons", max_length=60)
        modal.code = modal.text("Code (short, unique)", placeholder="falcons", required=False, max_length=20)
        modal.emoji = modal.text("Emoji (optional)", required=False, max_length=60)
        modal.color = modal.text("Colour (hex, optional)", placeholder="#3BA55D", required=False, max_length=7)
        await interaction.response.send_modal(modal)

    async def _submit_add(self, interaction: discord.Interaction, modal: FormModal) -> None:
        created = {}

        async def change() -> None:
            created.update(await self.repo.create_team(
                modal.code.value or modal.name.value,
                modal.name.value,
                emoji=(modal.emoji.value or "").strip() or None,
                color=parse_color(modal.color.value),
            ))

        ok = await self.apply(interaction, change, f"Team **{modal.name.value.strip()}** created.")
        if ok and created.get("id"):
            page = TeamPage(self.session, created["id"])
            page.notice = f"✅ **{created.get('name')}** created. Put it in a division below."
            self.session.stack.append(page)
            await page.show(interaction)


class TeamPage(DashboardPage):
    title = "Team"

    def __init__(self, session: Any, team_id: str) -> None:
        super().__init__(session)
        self.team_id = team_id
        self.confirm_archive = False

    async def load(self) -> None:
        self.team = await self.repo.get_team(self.team_id)
        self.season = LeagueCache.active_season()
        season_id = (self.season or {}).get("id")
        self.assignment = LeagueCache.assignment(self.team_id, season_id) if season_id else None
        self.division = LeagueCache.division((self.assignment or {}).get("division_id"))
        self.groups = LeagueCache.groups(self.division["id"]) if self.division else []

    def embed(self) -> discord.Embed:
        if not self.team:
            return discord.Embed(title="Team not found", color=EMBED_COLOR)
        t = self.team
        embed = discord.Embed(title=team_title(t), color=hex_to_int(t.get("color")) or EMBED_COLOR)
        owner = f"<@{t['owner_discord_id']}>" if t.get("owner_discord_id") else "No owner"
        lines = [f"**Owner:** {owner}", f"**Code:** `{t.get('code')}` · **Colour:** `{t.get('color') or 'default'}`"]
        if self.season:
            group = LeagueCache.group((self.assignment or {}).get("group_id"))
            placement = division_label(self.division) if self.division else "⚠️ Not in a division"
            if group:
                placement += f" · {group['name']}"
            lines.append(f"**{self.season.get('name')}:** {placement}")
        else:
            lines.append("No season yet: create one under **Season** to assign divisions.")
        if t.get("archived_at"):
            lines.append("🗄️ **Archived**")
        embed.description = "\n".join(lines)
        if t.get("logo_url"):
            embed.set_thumbnail(url=t["logo_url"])
        if self.confirm_archive:
            embed.add_field(
                name="⚠️ Archive?",
                value="The team leaves the current season (only if it has no games) and keeps its history.",
                inline=False,
            )
        return embed

    def build(self) -> None:
        if not self.team:
            return
        if self.season:
            options = [
                discord.SelectOption(
                    label=division_label(d)[:100],
                    value=d["id"],
                    default=self.division is not None and d["id"] == self.division["id"],
                )
                for d in LeagueCache.divisions()
            ]
            if self.division:
                options.append(discord.SelectOption(label="Remove from this season", value=REMOVE, emoji="➖"))
            self.select("Division this season...", options, self._set_division, row=0)
            if self.groups:
                current = (self.assignment or {}).get("group_id")
                group_options = [
                    discord.SelectOption(label=g["name"][:100], value=g["id"], default=g["id"] == current) for g in self.groups
                ]
                group_options.append(discord.SelectOption(label="No group", value=NO_GROUP, default=current is None))
                self.select("Group...", group_options, self._set_group, row=1)
        self.button("Edit info", self._edit_info, emoji="✏️", row=2)
        self.button("Set owner", self._set_owner, emoji="👤", row=2)
        self.button("Clear owner", self._clear_owner, row=2, disabled=not self.team.get("owner_discord_id"))
        self.button("Logo", self._logo, emoji="🖼️", row=2)
        if self.confirm_archive:
            self.button("Confirm archive", self._archive, style=discord.ButtonStyle.danger, row=3)
            self.button("Cancel", self._cancel_archive, row=3)
        else:
            self.button("Remove logo", self._remove_logo, row=3, disabled=not self.team.get("logo_url"))
            self.button("Archive", self._ask_archive, style=discord.ButtonStyle.danger, emoji="🗄️", row=3)

    # ------------------------------------------------------------------ season placement
    async def _set_division(self, interaction: discord.Interaction, value: str) -> None:
        season_id = self.season["id"]
        if value == REMOVE:
            await self.apply(
                interaction, lambda: self.repo.remove_team_from_season(season_id, self.team_id),
                "Removed from this season.", rosters=True,
            )
            return
        division = LeagueCache.division(value)
        await self.apply(
            interaction, lambda: self.repo.assign_team(season_id, self.team_id, value, None),
            f"Moved to **{division_label(division)}**.", rosters=True,
        )

    async def _set_group(self, interaction: discord.Interaction, value: str) -> None:
        group_id = None if value == NO_GROUP else value
        await self.apply(
            interaction,
            lambda: self.repo.assign_team(self.season["id"], self.team_id, self.division["id"], group_id),
            "Group updated.",
        )

    # ------------------------------------------------------------------ info
    async def _edit_info(self, interaction: discord.Interaction) -> None:
        t = self.team
        modal = FormModal(f"Edit {t.get('name')}", self._submit_info)
        modal.name = modal.text("Team name", default=t.get("name"), max_length=60)
        modal.code = modal.text("Code", default=t.get("code"), max_length=20)
        modal.emoji = modal.text("Emoji", default=t.get("emoji"), required=False, max_length=60)
        modal.color = modal.text("Colour (hex)", default=t.get("color"), required=False, max_length=7)
        await interaction.response.send_modal(modal)

    async def _submit_info(self, interaction: discord.Interaction, modal: FormModal) -> None:
        old_code = self.team.get("code")

        async def change() -> None:
            await self.repo.update_team(
                self.team_id,
                name=modal.name.value.strip(),
                code=modal.code.value,
                emoji=(modal.emoji.value or "").strip() or None,
                color=parse_color(modal.color.value),
            )

        if await self.apply(interaction, change, "Team updated.", rosters=True):
            updated = await self.repo.get_team(self.team_id) or {}
            if updated.get("code") != old_code:
                remove_cached_logo(old_code)  # re-downloaded under the new code on next sync

    # ------------------------------------------------------------------ owner
    async def _set_owner(self, interaction: discord.Interaction) -> None:
        modal = FormModal(f"Owner of {self.team.get('name')}", self._submit_owner)
        modal.user = discord.ui.UserSelect(min_values=1, max_values=1)
        modal.add_item(discord.ui.Label(text="New owner", description="A member can own one team.", component=modal.user))
        await interaction.response.send_modal(modal)

    async def _submit_owner(self, interaction: discord.Interaction, modal: FormModal) -> None:
        user = modal.user.values[0]

        async def change() -> None:
            if getattr(user, "bot", False):
                raise LeagueRuleError("INVALID", "Bots can't own teams.")
            current = TeamPlayerCache.get_team_by_owner(user.id)
            if current and current.get("id") != self.team_id:
                raise LeagueRuleError("DUPLICATE", f"{user.mention} already owns **{current.get('name')}**.")
            await self.repo.update_team(self.team_id, owner_discord_id=user.id)

        await self.apply(interaction, change, f"{user.mention} now owns this team.")

    async def _clear_owner(self, interaction: discord.Interaction) -> None:
        await self.apply(interaction, lambda: self.repo.update_team(self.team_id, owner_discord_id=None), "Owner removed.")

    # ------------------------------------------------------------------ logo
    async def _logo(self, interaction: discord.Interaction) -> None:
        modal = FormModal(f"Logo for {self.team.get('name')}", self._submit_logo)
        modal.upload = discord.ui.FileUpload(min_values=1, max_values=1, required=True)
        modal.add_item(discord.ui.Label(
            text="Logo image", description="PNG, JPEG or WebP. Square images look best.", component=modal.upload,
        ))
        await interaction.response.send_modal(modal)

    async def _submit_logo(self, interaction: discord.Interaction, modal: FormModal) -> None:
        attachment = modal.upload.values[0]

        async def change() -> None:
            try:
                url = await upload_team_logo(self.session.supabase, self.team, attachment)
            except LogoError as exc:
                raise LeagueRuleError("INVALID", str(exc)) from exc
            await self.repo.update_team(self.team_id, logo_url=url)

        await self.apply(interaction, change, "Logo updated.")

    async def _remove_logo(self, interaction: discord.Interaction) -> None:
        async def change() -> None:
            await self.repo.update_team(self.team_id, logo_url=None)
            remove_cached_logo(self.team.get("code"))

        await self.apply(interaction, change, "Logo removed.")

    # ------------------------------------------------------------------ archive
    async def _ask_archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = True
        await self.show(interaction)

    async def _cancel_archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = False
        await self.show(interaction)

    async def _archive(self, interaction: discord.Interaction) -> None:
        self.confirm_archive = False
        season_id = (self.season or {}).get("id")
        name = self.team.get("name")
        ok = await self.apply(
            interaction, lambda: self.repo.archive_team(self.team_id, season_id), f"**{name}** archived.", rosters=True,
        )
        if ok and len(self.session.stack) > 1:
            self.session.stack.pop()
            parent = self.session.stack[-1]
            parent.notice = f"✅ **{name}** archived."
            await parent.show(interaction)


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
