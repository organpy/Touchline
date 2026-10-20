"""Season panel for the admin dashboard.

Seasons are league-wide and move through phases (setup → regular → postseason → offseason →
completed). This panel creates the first season and lists past ones; the phase changes and the
rollover to the next season live in the dashboard's season lifecycle.
"""

from __future__ import annotations

import logging
from typing import Any, List

import discord
from discord.ext import commands

from cogs.admin.base import AdminBaseManager, AdminUIView
from db import get_supabase
from db.league_repository import SEASON_PHASE_LABELS, LeagueRepository, LeagueRuleError
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)

SEASONS_PER_PAGE = 10


def season_line(season: dict) -> str:
    marker = "🟢" if season.get("active") else "⚪"
    phase = SEASON_PHASE_LABELS.get(season.get("phase"), (season.get("phase") or "").title())
    champion = ""
    if season.get("champion_team_id"):
        from utils.team_player_cache import TeamPlayerCache

        team = TeamPlayerCache.get_team(season["champion_team_id"]) or {}
        champion = f" · 🏆 {team.get('name', 'Unknown')}"
    return f"{marker} **{season.get('name')}** (#{season.get('number')}) — {phase}{champion}"


class CreateSeasonModal(discord.ui.Modal, title="Create First Season"):
    season_name = discord.ui.TextInput(
        label="Season Name",
        placeholder="e.g. Season 1, 2026 Spring",
        default="Season 1",
        min_length=1,
        max_length=100,
    )

    def __init__(self, parent: "SeasonActionsView") -> None:
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            season = await LeagueRepository(get_supabase()).create_first_season(self.season_name.value)
        except LeagueRuleError as exc:
            await interaction.followup.send(f"❌ {exc.message}", ephemeral=True)
            return
        await LeagueCache.refresh()
        logger.info("Season %s created by %s", season.get("name"), interaction.user.id)
        await interaction.followup.send(
            f"✅ **{season['name']}** created in setup. Assign teams to divisions, then start the regular season.",
            ephemeral=True,
        )
        await self.parent.update_display()


class SeasonActionsView(AdminUIView):
    """Season overview with paging."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(AdminBaseManager(), guild_id, timeout=300)
        self.bot = bot
        self.supabase = supabase
        self.page = 0

    def _seasons(self) -> List[dict]:
        return LeagueCache.seasons()

    def create_embed(self) -> discord.Embed:
        seasons = self._seasons()
        embed = discord.Embed(title="Seasons", color=0x2B2D31)
        if not seasons:
            embed.description = "No seasons yet. **Create** the first season to start."
            return embed
        pages = max(1, (len(seasons) + SEASONS_PER_PAGE - 1) // SEASONS_PER_PAGE)
        self.page = min(self.page, pages - 1)
        chunk = seasons[self.page * SEASONS_PER_PAGE:(self.page + 1) * SEASONS_PER_PAGE]
        embed.description = "\n".join(season_line(s) for s in chunk)
        embed.set_footer(text=f"Page {self.page + 1}/{pages} · Seasons are league-wide")
        return embed

    def _sync_buttons(self) -> None:
        seasons = self._seasons()
        self.create_season.disabled = bool(seasons)
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * SEASONS_PER_PAGE >= len(seasons)

    async def update_display(self) -> None:
        self._sync_buttons()
        await super().update_display()

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=0)
    async def create_season(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CreateSeasonModal(self))

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Back", emoji="🔙", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading (UI views only)."""
    logger.info("✅ Seasons module loaded (no cog class needed - provides UI views only)")
