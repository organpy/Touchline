"""``/demo``: admin-only test commands that run on made-up data. Nothing is read from or written to the database.

Off by default. Set ``ENABLE_DEMO_COMMANDS=true`` in ``.env`` (a dev server) to register them; on the live bot
leave it unset so members never see the commands.

* ``/demo bracket``     a bracket image in a chosen state (any kind, 2-16 teams, best-of, progress)
* ``/demo playoffs``    an interactive playoff: play games, finish series/rounds, forfeits, seed swaps, reset
* ``/demo standings``   ``/demo match-table``   ``/demo player-card``: each image with dummy data
* ``/demo gallery``     every preview image at once (same set as ``scripts/preview_images.py``)
* ``/demo errors``      the error, permission, rate-limit and validation replies
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION, round_name
from utils import demo_data as demo
from utils.bracket_image_generator import generate_bracket_image
from utils.command_limits import HEAVY
from utils.error_embeds import (
    database_error_embed,
    error_embed,
    not_found_embed,
    permission_error_embed,
    rate_limit_embed,
    timeout_error_embed,
    validation_error_embed,
)
from utils.match_table_image_generator import generate_match_table_image
from utils.permissions import AdminOnlyView, admin_only
from utils.player_card_image_generator import generate_player_card_image
from utils.render.limit import render_in_thread
from utils.standings_image_generator import generate_simple_standings_image

logger = logging.getLogger(__name__)

ENV_FLAG = "ENABLE_DEMO_COMMANDS"
EMBED_COLOR = 0x2B2D31
SIM_TIMEOUT = 900
FILES_PER_MESSAGE = 10

KIND_CHOICES = [
    app_commands.Choice(name="Championship", value=KIND_CHAMPIONSHIP),
    app_commands.Choice(name="Promotion playoff (winners go up)", value=KIND_PROMOTION),
    app_commands.Choice(name="Relegation playoff (losers go down)", value=KIND_RELEGATION),
    app_commands.Choice(name="Promotion/relegation series", value=KIND_PROMO_REL),
]
KIND_TITLES = {
    KIND_CHAMPIONSHIP: ("Championship", "D1 · Premier · Demo season", "#F7AD44", None),
    KIND_PROMOTION: ("Promotion playoff", "D2 · Championship · Demo season", "#5B8DEF", None),
    KIND_RELEGATION: ("Relegation playoff", "D1 · Premier · Demo season", "#E8655B", None),
    KIND_PROMO_REL: ("Promotion/relegation", "D1 · Premier v D2 · Championship", "#4CAF7A", "Plays in D1"),
}
PROGRESS_CHOICES = [
    app_commands.Choice(name="Not started", value="none"),
    app_commands.Choice(name="One game played", value="game"),
    app_commands.Choice(name="First round done", value="round"),
    app_commands.Choice(name="Finished", value="done"),
]
BEST_OF_CHOICES = [app_commands.Choice(name=f"Best of {n}", value=n) for n in (1, 3, 5, 7)]
CARD_CHOICES = [
    app_commands.Choice(name="Veteran (three seasons)", value="veteran"),
    app_commands.Choice(name="No games this season", value="idle"),
    app_commands.Choice(name="Rookie (no games yet)", value="rookie"),
    app_commands.Choice(name="Very long names", value="long"),
]


def demo_enabled() -> bool:
    return os.getenv(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def png_file(buffer: Optional[io.BytesIO], filename: str) -> Optional[discord.File]:
    if buffer is None:
        return None
    return discord.File(io.BytesIO(buffer.getvalue()), filename=filename)


def render_demo_bracket(bracket: Dict[str, Any]) -> Optional[io.BytesIO]:
    title, subtitle, accent, label = KIND_TITLES[bracket["kind"]]
    return generate_bracket_image(bracket, demo.team_map(), title, subtitle, accent, result_label=label)


def name_of(team_id: Optional[str]) -> str:
    return (demo.team_map().get(team_id) or {}).get("name", "TBD") if team_id else "TBD"


async def send_image(interaction: discord.Interaction, buffer: Optional[io.BytesIO], filename: str, title: str,
                     note: Optional[str] = None) -> None:
    file = png_file(buffer, filename)
    if file is None:
        await interaction.followup.send("The image failed to render. The error is in the bot log.", ephemeral=True)
        return
    embed = discord.Embed(title=title, description=note, color=EMBED_COLOR)
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="Demo data · nothing was saved")
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


# --------------------------------------------------------------------------------------- playoff simulator
class PlayoffSimView(AdminOnlyView):
    """An ephemeral playoff that admins push forward with buttons, redrawn after every step."""

    def __init__(self, sim: demo.BracketSim) -> None:
        super().__init__(timeout=SIM_TIMEOUT)
        self.sim = sim
        self.notice: Optional[str] = None
        self.build()

    # ------------------------------------------------------------------ components
    def build(self) -> None:
        self.clear_items()
        open_series = bool(self.sim.ready())
        for label, handler, style in (
            ("Play game", self._game, discord.ButtonStyle.primary),
            ("Finish series", self._series, discord.ButtonStyle.secondary),
            ("Finish round", self._round, discord.ButtonStyle.secondary),
            ("Play to the end", self._all, discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(label=label, style=style, disabled=not open_series, row=0)
            button.callback = handler
            self.add_item(button)
        reset = discord.ui.Button(label="Reset", style=discord.ButtonStyle.danger, row=0, disabled=not self.sim.started)
        reset.callback = self._reset
        self.add_item(reset)

        forfeits = [
            discord.SelectOption(
                label=f"{name_of(s[f'team{side}_id'])} advance by forfeit"[:100],
                description=f"{round_label(self.sim.bracket, s)}: {name_of(s['team1_id'])} v {name_of(s['team2_id'])}"[:100],
                value=f"{s['id']}|{s[f'team{side}_id']}",
            )
            for s in self.sim.ready() for side in (1, 2)
        ][:25]
        if forfeits:
            select = discord.ui.Select(placeholder="Decide a series by forfeit...", options=forfeits, row=1)
            select.callback = lambda interaction: self._forfeit(interaction, select.values[0])
            self.add_item(select)

        if not self.sim.started:
            entries = sorted(self.sim.bracket["bracket_entries"], key=lambda e: e["seed"])[:25]
            swap = discord.ui.Select(
                placeholder="Swap two seeds (only before the first game)...", min_values=2, max_values=2, row=2,
                options=[discord.SelectOption(label=f"({e['seed']}) {name_of(e['team_id'])}"[:100], value=e["team_id"])
                         for e in entries],
            )
            swap.callback = lambda interaction: self._swap(interaction, swap.values)
            self.add_item(swap)

    def embed(self) -> discord.Embed:
        bracket = self.sim.bracket
        title = KIND_TITLES[bracket["kind"]][0]
        status = "Finished" if self.sim.finished else f"{len(self.sim.ready())} series ready"
        lines = [f"{status} · {self.sim.games_played} games played"]
        if self.notice:
            lines.append(self.notice)
            self.notice = None
        if self.sim.log:
            lines.append("")
            lines.extend(self.sim.log[-4:])
        embed = discord.Embed(title=f"Demo · {title}", description="\n".join(lines), color=EMBED_COLOR)
        embed.set_image(url="attachment://demo-bracket.png")
        embed.set_footer(text="Demo data · nothing was saved")
        return embed

    async def show(self, interaction: discord.Interaction) -> None:
        self.build()
        buffer = await render_in_thread(render_demo_bracket, self.sim.bracket)
        file = png_file(buffer, "demo-bracket.png")
        await interaction.response.edit_message(embed=self.embed(), view=self, attachments=[file] if file else [])

    # ------------------------------------------------------------------ actions
    async def _game(self, interaction: discord.Interaction) -> None:
        self.sim.play_game()
        await self.show(interaction)

    async def _series(self, interaction: discord.Interaction) -> None:
        played = self.sim.play_series()
        self.notice = f"Series decided in {played} game{'s' if played != 1 else ''}."
        await self.show(interaction)

    async def _round(self, interaction: discord.Interaction) -> None:
        decided = self.sim.play_round()
        self.notice = f"{decided} series decided."
        await self.show(interaction)

    async def _all(self, interaction: discord.Interaction) -> None:
        self.sim.play_all()
        await self.show(interaction)

    async def _reset(self, interaction: discord.Interaction) -> None:
        self.sim.reset()
        self.notice = "Back to the start."
        await self.show(interaction)

    async def _forfeit(self, interaction: discord.Interaction, value: str) -> None:
        series_id, winner = value.split("|", 1)
        self.sim.forfeit(series_id, winner)
        self.notice = f"**{name_of(winner)}** advance by forfeit."
        await self.show(interaction)

    async def _swap(self, interaction: discord.Interaction, team_ids: List[str]) -> None:
        a, b = team_ids
        if self.sim.swap_seeds(a, b):
            self.notice = f"Swapped **{name_of(a)}** and **{name_of(b)}**."
        else:
            self.notice = "Seeds can only change before the first game."
        await self.show(interaction)


def round_label(bracket: Dict[str, Any], series: Dict[str, Any]) -> str:
    last = max(s["round"] for s in bracket["series"])
    return round_name(series["round"], last, bracket["kind"])


# --------------------------------------------------------------------------------------- commands
@app_commands.default_permissions(administrator=True)
class DemoCog(commands.GroupCog, name="demo", group_name="demo",
              group_description="[ADMIN] Test views with made-up data (nothing is saved)"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="bracket", description="[ADMIN] Draw a demo playoff bracket in a chosen state", extras=HEAVY)
    @app_commands.describe(kind="Kind of bracket", teams="Teams in the playoff (2-16)", spots="Promotion/relegation spots",
                           best_of="Games per series", progress="How far the bracket has been played")
    @app_commands.choices(kind=KIND_CHOICES, best_of=BEST_OF_CHOICES, progress=PROGRESS_CHOICES)
    @admin_only()
    async def bracket(
        self,
        interaction: discord.Interaction,
        kind: str = KIND_CHAMPIONSHIP,
        teams: app_commands.Range[int, 2, 16] = 8,
        spots: app_commands.Range[int, 1, 8] = 1,
        best_of: int = 1,
        progress: str = "round",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            sim = demo.BracketSim(demo.demo_bracket(kind, teams, spots, best_of), seed=teams * 31 + spots, name=name_of)
        except ValueError as exc:
            await interaction.followup.send(embed=validation_error_embed("teams", str(exc)), ephemeral=True)
            return
        if progress == "game":
            sim.play_game()
        elif progress == "round":
            sim.play_round()
        elif progress == "done":
            sim.play_all()
        buffer = await render_in_thread(render_demo_bracket, sim.bracket)
        await send_image(interaction, buffer, "demo-bracket.png", f"Demo · {KIND_TITLES[kind][0]}",
                         f"{teams} teams · best of {best_of} · {sim.games_played} games played")

    @app_commands.command(name="playoffs", description="[ADMIN] Play a demo playoff step by step", extras=HEAVY)
    @app_commands.describe(kind="Kind of bracket", teams="Teams in the playoff (2-16)", spots="Promotion/relegation spots",
                           best_of="Games per series")
    @app_commands.choices(kind=KIND_CHOICES, best_of=BEST_OF_CHOICES)
    @admin_only()
    async def playoffs(
        self,
        interaction: discord.Interaction,
        kind: str = KIND_CHAMPIONSHIP,
        teams: app_commands.Range[int, 2, 16] = 8,
        spots: app_commands.Range[int, 1, 8] = 1,
        best_of: int = 3,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            sim = demo.BracketSim(demo.demo_bracket(kind, teams, spots, best_of), name=name_of)
        except ValueError as exc:
            await interaction.followup.send(embed=validation_error_embed("teams", str(exc)), ephemeral=True)
            return
        view = PlayoffSimView(sim)
        buffer = await render_in_thread(render_demo_bracket, sim.bracket)
        file = png_file(buffer, "demo-bracket.png")
        if file is None:
            await interaction.followup.send("The image failed to render. The error is in the bot log.", ephemeral=True)
            return
        await interaction.followup.send(embed=view.embed(), file=file, view=view, ephemeral=True)

    @app_commands.command(name="standings", description="[ADMIN] Draw a demo standings image", extras=HEAVY)
    @app_commands.describe(teams="Teams in the division (1-20)", form="Show the last-5 column", zones="Colour the zones")
    @admin_only()
    async def standings(self, interaction: discord.Interaction, teams: app_commands.Range[int, 1, 20] = 8,
                        form: bool = True, zones: bool = True) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = demo.standings_rows(teams, with_zones=zones)
        buffer = await render_in_thread(generate_simple_standings_image, rows, "Demo season", "D1 · Premier", None,
                                        demo.form_for(rows) if form else None, "#5865F2")
        await send_image(interaction, buffer, "demo-standings.png", "Demo · Standings")

    @app_commands.command(name="match-table", description="[ADMIN] Draw a demo match table", extras=HEAVY)
    @app_commands.describe(teams="Teams in the division (2-10)", games="Games between each pair (1-10)")
    @admin_only()
    async def match_table(self, interaction: discord.Interaction, teams: app_commands.Range[int, 2, 10] = 4,
                          games: app_commands.Range[int, 1, 10] = 2) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        team_rows = demo.match_table_teams(teams)
        matches = demo.played_games([t["id"] for t in team_rows], games)
        buffer = await render_in_thread(generate_match_table_image, matches, team_rows, "Demo season", "D1 · Premier",
                                        None, games)
        await send_image(interaction, buffer, "demo-match-table.png", "Demo · Match table")

    @app_commands.command(name="player-card", description="[ADMIN] Draw a demo player card", extras=HEAVY)
    @app_commands.describe(variant="Which player")
    @app_commands.choices(variant=CARD_CHOICES)
    @admin_only()
    async def player_card(self, interaction: discord.Interaction, variant: str = "veteran") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        career = demo.career()
        args = {
            "veteran": ("Ace", "Frost Falcons", "frost-falcons", "D1", "Demo season", career[2], career),
            "idle": ("Zed", "The Herd", "the-herd", "D2", "Demo season", None, career[:2]),
            "rookie": ("New Player", None, None, None, "Demo season", None, []),
            "long": ("A Player With An Impossibly Long Display Name Indeed",
                     "The Extraordinarily Long Named Herd of Mighty Bison", "no-such-logo", "D1",
                     "Demo season (Winter Championship Edition)", career[2], career),
        }[variant]
        buffer = await render_in_thread(generate_player_card_image, *args)
        await send_image(interaction, buffer, "demo-player-card.png", "Demo · Player card")

    @app_commands.command(name="gallery", description="[ADMIN] Post every demo image at once", extras=HEAVY)
    @admin_only()
    async def gallery(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        from utils.demo_gallery import build

        images = await render_in_thread(build)
        files = [png_file(buffer, f"{name}.png") for name, _, buffer in images]
        failed = [name for (name, _, _), file in zip(images, files) if file is None]
        files = [file for file in files if file is not None]
        for start in range(0, len(files), FILES_PER_MESSAGE):
            await interaction.followup.send(files=files[start:start + FILES_PER_MESSAGE], ephemeral=True)
        summary = f"{len(files)} images rendered."
        if failed:
            summary += f" Failed: {', '.join(failed)}."
        await interaction.followup.send(summary, ephemeral=True)

    @app_commands.command(name="errors", description="[ADMIN] Show every kind of error reply")
    @admin_only()
    async def errors(self, interaction: discord.Interaction) -> None:
        embeds = [
            error_embed("Something went wrong", "An unexpected error occurred while running this command.",
                        hint="The error has been logged. Please try again or contact an admin."),
            not_found_embed("Player", "Nobody"),
            permission_error_embed(),
            database_error_embed(),
            timeout_error_embed(),
            rate_limit_embed(12),
            validation_error_embed("score", "must be a whole number"),
        ]
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    if not demo_enabled():
        logger.info("Demo commands off (set %s=true to turn them on)", ENV_FLAG)
        return
    await bot.add_cog(DemoCog(bot))
