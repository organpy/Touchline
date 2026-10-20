"""Draft picks management UI for the admin dashboard.

Only divisions with ``picks_enabled`` run a draft. Every flow starts the same way: choose one of
those divisions, then a season; the teams offered are the ones that played in that division that
season.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord

from cogs.admin.common import LazyInitView
from db.db_utils import execute_async
from db.transactions_repository import TransactionsRepository
from utils.league_cache import LeagueCache, hex_to_int
from utils.team_player_cache import TeamPlayerCache
from utils.team_picker import team_select_for

logger = logging.getLogger(__name__)

MAX_ROUNDS = 10
PICK_COLOR = discord.Color.purple()


def pick_label(pick: Dict[str, Any]) -> str:
    return f"#{pick.get('overall', '?')} · Round {pick.get('round', '?')}"


def team_label(team: Optional[Dict[str, Any]], fallback_id: Any = None) -> str:
    if team:
        return f"{team.get('emoji') or ''} {team.get('name')} (`{team.get('code')}`)".strip()
    return f"Team {str(fallback_id or '?')[:8]}"


def team_color(team: Optional[Dict[str, Any]]) -> discord.Color:
    value = hex_to_int((team or {}).get('color'))
    return discord.Color(value) if value is not None else PICK_COLOR


def build_pick_rows(teams: List[Dict[str, Any]], rounds: int, *, season_id: str, division_id: str) -> List[Dict[str, Any]]:
    """Draft picks where every team picks once per round, in the given team order."""
    rows: List[Dict[str, Any]] = []
    overall = 1
    for round_num in range(1, rounds + 1):
        for team in teams:
            rows.append({
                'season_id': season_id,
                'division_id': division_id,
                'round': round_num,
                'overall': overall,
                'current_owner_team_id': team['id'],
                'original_team_id': team['id'],
            })
            overall += 1
    return rows


async def division_teams(division_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Teams that played in the division in that season, sorted by name."""
    await LeagueCache.load_season_teams(season_id)
    team_ids = LeagueCache.team_ids_in_division(division_id, season_id)
    teams = [TeamPlayerCache.get_team(team_id) for team_id in team_ids]
    return sorted((t for t in teams if t), key=lambda t: (t.get('name') or '').lower())


class PicksActionsView(discord.ui.View):
    """Buttons for draft picks management."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.original_message: Optional[discord.Message] = None
        self.repo = TransactionsRepository(supabase)

    def create_embed(self) -> discord.Embed:
        divisions = LeagueCache.picks_divisions()
        enabled = ", ".join(d.get('code') or d.get('name') for d in divisions) or "none"
        embed = discord.Embed(
            title="Draft Picks",
            description=f"Seed, reassign, and view draft picks.\nDivisions with a draft: **{enabled}**",
            color=0x2B2D31,
        )
        embed.add_field(
            name="Actions",
            value=(
                "**Seed** - Generate picks for a division and season\n"
                "**Reassign** - Transfer ownership\n"
                "**View** - Draft order\n"
                "**By Team** - Team pick ownership"
            ),
            inline=False,
        )
        embed.set_footer(text="Turn the draft on per division in Dashboard → Divisions → Rules.")
        return embed

    async def update_display(self) -> None:
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update draft picks management embed: %s", exc)

    async def _open(self, interaction: discord.Interaction, view: "DivisionSeasonFlow", title: str, text: str) -> None:
        await view.prepare()
        embed = discord.Embed(title=title, description=text, color=PICK_COLOR)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Seed", style=discord.ButtonStyle.secondary, row=0)
    async def seed_picks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open(interaction, SeedPicksView(self.supabase, self, self.repo),
                         "🌱 Seed Draft Picks", "Choose a division and season, then the number of rounds.")

    @discord.ui.button(label="Reassign", style=discord.ButtonStyle.secondary, row=0)
    async def reassign_pick(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open(interaction, ReassignPickView(self.supabase, self, self.repo),
                         "🔄 Reassign Draft Pick", "Choose a division and season, then the pick and its new owner.")

    @discord.ui.button(label="View", style=discord.ButtonStyle.secondary, row=0)
    async def view_picks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open(interaction, ViewPicksView(self.supabase, self, self.repo),
                         "📋 View All Picks", "Choose a division and season to see the draft order.")

    @discord.ui.button(label="By Team", style=discord.ButtonStyle.secondary, row=0)
    async def view_by_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._open(interaction, ViewPicksByTeamView(self.supabase, self, self.repo),
                         "🗂️ View Team Picks", "Choose a division and season, then a team.")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


class DivisionSeasonFlow(LazyInitView):
    """Shared first steps: a draft division, then a season. Subclasses implement ``on_season``."""

    def __init__(self, supabase: Any, parent_view: PicksActionsView, repo: TransactionsRepository) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.repo = repo
        self.original_message: Optional[discord.Message] = None
        self.division: Optional[Dict[str, Any]] = None
        self.season: Optional[Dict[str, Any]] = None
        self.teams: List[Dict[str, Any]] = []
        self.team_map: Dict[str, Dict[str, Any]] = {}

    async def populate(self) -> None:
        await LeagueCache.ensure_ready()
        divisions = LeagueCache.picks_divisions()
        if not divisions:
            self.add_item(discord.ui.Select(
                placeholder="No division runs a draft",
                options=[discord.SelectOption(label="Enable picks in Dashboard → Divisions", value="none")],
                disabled=True,
            ))
            return
        select = discord.ui.Select(
            placeholder="Select division...",
            options=[
                discord.SelectOption(
                    label=f"{d.get('code')} · {d.get('name')}"[:100],
                    value=d['id'],
                    description=f"Tier {d.get('tier')}",
                    emoji=d.get('emoji') or None,
                )
                for d in divisions[:25]
            ],
            row=0,
        )
        select.callback = self.select_division
        self.add_item(select)

    async def select_division(self, interaction: discord.Interaction) -> None:
        self.division = LeagueCache.division(interaction.data['values'][0])
        self.season = None
        if not self.division:
            await interaction.response.send_message("❌ Division not found.", ephemeral=True)
            return
        seasons = LeagueCache.seasons()
        if not seasons:
            await interaction.response.send_message("❌ No seasons yet. Create one in the dashboard.", ephemeral=True)
            return
        select = discord.ui.Select(
            placeholder="Select season...",
            options=[
                discord.SelectOption(
                    label=(season.get('name') or f"Season {season.get('number')}")[:100],
                    value=season['id'],
                    description="✅ Active" if season.get('active') else (season.get('phase') or '').title(),
                )
                for season in seasons[:25]
            ],
        )
        select.callback = self.select_season
        view = discord.ui.View(timeout=300)
        view.add_item(select)
        await interaction.response.send_message(
            f"**{self.division.get('name')}** selected. Choose a season:", view=view, ephemeral=True,
        )

    async def select_season(self, interaction: discord.Interaction) -> None:
        if not self.division:
            await interaction.response.send_message("❌ Please select a division first.", ephemeral=True)
            return
        self.season = LeagueCache.season(interaction.data['values'][0])
        if not self.season:
            await interaction.response.send_message("❌ Season not found.", ephemeral=True)
            return
        self.teams = await division_teams(self.division['id'], self.season['id'])
        self.team_map = {t['id']: t for t in self.teams}
        await self.on_season(interaction)

    async def on_season(self, interaction: discord.Interaction) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def picks(self) -> List[Dict[str, Any]]:
        return await self.repo.list_draft_picks(self.season['id'], self.division['id'])

    @property
    def context_label(self) -> str:
        return f"{self.division.get('name')} · {self.season.get('name')}"


class SeedPicksView(DivisionSeasonFlow):
    """Seed draft picks: Division → Season → Rounds → Confirm."""

    async def on_season(self, interaction: discord.Interaction) -> None:
        existing = await self.picks()
        if existing:
            await interaction.response.send_message(
                f"⚠️ **{self.context_label}** already has {len(existing)} draft picks.", ephemeral=True,
            )
            return
        if len(self.teams) < 2:
            await interaction.response.send_message(
                f"❌ {self.division.get('name')} needs at least 2 teams in {self.season.get('name')}.", ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoundsModal(self))


class RoundsModal(discord.ui.Modal, title="Seed Draft Picks"):
    num_rounds = discord.ui.TextInput(label="Number of rounds", placeholder="e.g. 2", default="1", max_length=2)

    def __init__(self, flow: SeedPicksView) -> None:
        super().__init__()
        self.flow = flow

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            rounds = int(self.num_rounds.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Enter a whole number of rounds.", ephemeral=True)
            return
        if not 1 <= rounds <= MAX_ROUNDS:
            await interaction.response.send_message(f"❌ Rounds must be between 1 and {MAX_ROUNDS}.", ephemeral=True)
            return

        flow = self.flow
        rows = build_pick_rows(flow.teams, rounds, season_id=flow.season['id'], division_id=flow.division['id'])
        order = "\n".join(f"{i}. {team_label(t)}" for i, t in enumerate(flow.teams[:20], start=1))
        if len(flow.teams) > 20:
            order += f"\n… and {len(flow.teams) - 20} more"
        embed = discord.Embed(
            title="✅ Confirm Seed Draft Picks",
            description=(
                f"**{flow.context_label}**\n"
                f"**Teams**: {len(flow.teams)} · **Rounds**: {rounds} · **Total picks**: {len(rows)}\n\n"
                f"**Order each round**\n{order}"
            ),
            color=PICK_COLOR,
        )
        await interaction.response.send_message(embed=embed, view=ConfirmSeedView(flow, rows, rounds), ephemeral=True)


class ConfirmSeedView(discord.ui.View):
    def __init__(self, flow: SeedPicksView, rows: List[Dict[str, Any]], rounds: int) -> None:
        super().__init__(timeout=60)
        self.flow = flow
        self.rows = rows
        self.rounds = rounds

    @discord.ui.button(label="Confirm Seed", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = await execute_async(self.flow.supabase.table('draft_picks').insert(self.rows))
            if not result.data:
                raise RuntimeError("No data returned from insert")
            logger.info("✅ Seeded %d draft picks for %s by %s", len(self.rows), self.flow.context_label, interaction.user.id)
            await interaction.followup.send(
                f"✅ Seeded **{len(self.rows)}** draft picks for **{self.flow.context_label}** "
                f"({len(self.flow.teams)} teams × {self.rounds} rounds)",
                ephemeral=True,
            )
            await self.flow.parent_view.update_display()
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to seed picks: %s", exc)
            await interaction.followup.send("❌ Failed to seed picks. Check server logs.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)
        self.stop()


class ReassignPickView(DivisionSeasonFlow):
    """Reassign pick ownership within the division."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pick: Optional[Dict[str, Any]] = None

    async def on_season(self, interaction: discord.Interaction) -> None:
        picks = await self.picks()
        if not picks:
            await interaction.response.send_message("❌ No draft picks found for this division and season.", ephemeral=True)
            return
        self._picks = {p['id']: p for p in picks}
        select = discord.ui.Select(
            placeholder="Select pick to reassign...",
            options=[
                discord.SelectOption(
                    label=pick_label(p)[:100],
                    value=p['id'],
                    description=f"Owner: {(self.team_map.get(p.get('current_owner_team_id')) or {}).get('code', '?')}",
                )
                for p in picks[:25]
            ],
        )
        select.callback = self.select_pick
        view = discord.ui.View(timeout=300)
        view.add_item(select)
        note = f" (showing the first 25 of {len(picks)})" if len(picks) > 25 else ""
        await interaction.response.send_message(
            f"**{self.context_label}**: choose a pick{note}:", view=view, ephemeral=True,
        )

    async def select_pick(self, interaction: discord.Interaction) -> None:
        self.pick = self._picks.get(interaction.data['values'][0])
        if not self.pick:
            await interaction.response.send_message("❌ Pick not found.", ephemeral=True)
            return
        candidates = [t for t in self.teams if t['id'] != self.pick.get('current_owner_team_id')]
        if not candidates:
            await interaction.response.send_message("❌ No other teams in this division.", ephemeral=True)
            return
        select = discord.ui.Select(
            placeholder="Select new owner...",
            options=[
                discord.SelectOption(label=t.get('name', 'Unknown')[:100], value=t['id'], description=t.get('code'))
                for t in candidates[:25]
            ],
        )
        select.callback = self.select_new_owner
        view = discord.ui.View(timeout=300)
        view.add_item(select)
        owner = self.team_map.get(self.pick.get('current_owner_team_id'))
        embed = discord.Embed(
            title="Current Pick Details",
            description=f"**Pick**: {pick_label(self.pick)}\n**Current owner**: {team_label(owner, self.pick.get('current_owner_team_id'))}",
            color=PICK_COLOR,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def select_new_owner(self, interaction: discord.Interaction) -> None:
        new_team = self.team_map.get(interaction.data['values'][0])
        if not self.pick or not new_team:
            await interaction.response.send_message("❌ Please select a pick and team again.", ephemeral=True)
            return
        owner = self.team_map.get(self.pick.get('current_owner_team_id'))
        embed = discord.Embed(
            title="✅ Confirm Reassign",
            description=(
                f"**Pick**: {pick_label(self.pick)}\n"
                f"**From**: {team_label(owner, self.pick.get('current_owner_team_id'))}\n"
                f"**To**: {team_label(new_team)}"
            ),
            color=team_color(new_team),
        )
        await interaction.response.send_message(embed=embed, view=ConfirmReassignView(self, self.pick, new_team), ephemeral=True)


class ConfirmReassignView(discord.ui.View):
    def __init__(self, flow: ReassignPickView, pick: Dict[str, Any], new_team: Dict[str, Any]) -> None:
        super().__init__(timeout=60)
        self.flow = flow
        self.pick = pick
        self.new_team = new_team

    @discord.ui.button(label="Confirm Reassign", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await self.flow.repo.update_draft_pick_owner(self.pick['id'], self.new_team['id'])
        if result:
            logger.info("✅ Draft pick %s reassigned to %s by %s", self.pick['id'], self.new_team.get('code'), interaction.user.id)
            self.pick['current_owner_team_id'] = self.new_team['id']
            await interaction.followup.send(f"✅ Pick reassigned to **{team_label(self.new_team)}**", ephemeral=True)
            await self.flow.parent_view.update_display()
        else:
            await interaction.followup.send("❌ Failed to reassign pick. Check server logs.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("❌ Cancelled.", ephemeral=True)
        self.stop()


class ViewPicksView(DivisionSeasonFlow):
    """Draft order for a division and season."""

    async def on_season(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        picks = await self.picks()
        if not picks:
            await interaction.followup.send("❌ No draft picks found.", ephemeral=True)
            return
        for start in range(0, len(picks), 20):
            chunk = picks[start:start + 20]
            embed = discord.Embed(
                title=f"📋 {self.context_label} · Draft Order",
                description=f"{start + 1}-{start + len(chunk)} of {len(picks)}",
                color=PICK_COLOR,
            )
            for pick in chunk:
                owner = self.team_map.get(pick.get('current_owner_team_id'))
                original = self.team_map.get(pick.get('original_team_id'))
                value = team_label(owner, pick.get('current_owner_team_id'))
                if pick.get('original_team_id') != pick.get('current_owner_team_id'):
                    value += f"\nvia {(original or {}).get('code', '?')}"
                embed.add_field(name=pick_label(pick), value=value, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)


class ViewPicksByTeamView(DivisionSeasonFlow):
    """Picks owned by one team."""

    async def on_season(self, interaction: discord.Interaction) -> None:
        if not self.teams:
            await interaction.response.send_message("❌ No teams in this division that season.", ephemeral=True)
            return
        # Already one division, so this only pages when the division has more than 25 teams
        select = team_select_for(self.teams, on_pick=self.view_team_picks, placeholder="Select team...")
        view = discord.ui.View(timeout=300)
        view.add_item(select)
        await interaction.response.send_message(f"**{self.context_label}**: choose a team:", view=view, ephemeral=True)

    async def view_team_picks(self, interaction: discord.Interaction) -> None:
        team = self.team_map.get(interaction.data['values'][0])
        if not team:
            await interaction.response.send_message("❌ Team not found.", ephemeral=True)
            return
        picks = [p for p in await self.picks() if p.get('current_owner_team_id') == team['id']]
        embed = discord.Embed(
            title=f"🗂️ {team.get('name')} Draft Picks",
            description=f"**{self.context_label}**",
            color=team_color(team),
        )
        if picks:
            lines = []
            for p in picks:
                line = pick_label(p)
                if p.get('original_team_id') != team['id']:
                    line += f" (via {(self.team_map.get(p.get('original_team_id')) or {}).get('code', '?')})"
                lines.append(line)
            embed.add_field(name=f"Picks ({len(picks)})", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Picks", value="No picks owned in this season.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
