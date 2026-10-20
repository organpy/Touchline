"""``/help`` (browse and search commands) and ``/admin-help`` (how to run a league).

Commands are grouped by the ``[TAG]`` at the start of their description, so new commands show up in
the right category without touching this file.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from utils.branding import get_league_name

logger = logging.getLogger(__name__)

PAGE_SIZE = 8


class CommandCategory(Enum):
    """Command categories: (title, description)."""

    STATS = ("📊 Stats", "Standings, leaderboards, player and team stats")
    TEAM_OWNER = ("👔 Team Owner", "Signings, releases, trades and draft picks for your team")
    MEMBERS = ("👥 Members", "League information for everyone")
    SEARCH = ("🔍 Search", "Find players and teams")
    SCORE = ("🏀 Score", "Report game results and player stats")
    ADMIN = ("⚙️ Admin", "Running the league (most of it lives in /dashboard)")
    HELP = ("❓ Help", "Help and guides")
    OTHER = ("📌 Other", "Everything else")


TAG_CATEGORIES = {
    "STATS": CommandCategory.STATS,
    "TEAM OWNER": CommandCategory.TEAM_OWNER,
    "OWNER": CommandCategory.TEAM_OWNER,
    "MEMBERS": CommandCategory.MEMBERS,
    "SEARCH": CommandCategory.SEARCH,
    "SCORE": CommandCategory.SCORE,
    "ADMIN": CommandCategory.ADMIN,
    "HELP": CommandCategory.HELP,
}


def strip_tag(description: str) -> str:
    if description.startswith("[") and "]" in description:
        return description[description.index("]") + 1:].strip()
    return description


def category_for(name: str, description: str) -> CommandCategory:
    if description.startswith("[") and "]" in description:
        tag = description[1:description.index("]")].upper()
        if tag in TAG_CATEGORIES:
            return TAG_CATEGORIES[tag]
    if name.startswith("report-"):
        return CommandCategory.SCORE
    if name in ("help", "admin-help"):
        return CommandCategory.HELP
    return CommandCategory.OTHER


def categorize(tree: app_commands.CommandTree) -> Dict[CommandCategory, List[Tuple[str, str]]]:
    categories: Dict[CommandCategory, List[Tuple[str, str]]] = {cat: [] for cat in CommandCategory}
    for cmd in tree.walk_commands():
        description = cmd.description or "No description"
        categories[category_for(cmd.qualified_name, description)].append((cmd.qualified_name, strip_tag(description)))
    for entries in categories.values():
        entries.sort()
    return categories


def overview_embed(categories: Dict[CommandCategory, List[Tuple[str, str]]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"📚 {get_league_name()} Commands",
        description="Pick a category below. `/help command:<name>` explains one command; `/help search:<word>` finds commands.",
        color=discord.Color.blurple(),
    )
    for category in CommandCategory:
        entries = categories.get(category) or []
        if entries:
            name, description = category.value
            embed.add_field(name=f"{name} ({len(entries)})", value=description, inline=False)
    return embed


class HelpView(discord.ui.View):
    """Category browser that edits the help message in place."""

    def __init__(self, bot: commands.Bot, categories: Dict, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.categories = categories
        self.current_category: Optional[CommandCategory] = None
        self.current_page = 0
        self._build()

    def _pages(self) -> int:
        entries = self.categories.get(self.current_category) or []
        return max(1, -(-len(entries) // PAGE_SIZE))

    def _build(self) -> None:
        self.clear_items()
        options = [
            discord.SelectOption(
                label=category.value[0][2:].strip()[:100],
                emoji=category.value[0].split()[0],
                value=category.name,
                description=f"{len(self.categories[category])} commands",
                default=category is self.current_category,
            )
            for category in CommandCategory
            if self.categories.get(category)
        ]
        if options:
            select = discord.ui.Select(placeholder="Choose a category...", options=options, row=0)
            select.callback = self._choose
            self.add_item(select)
        if self.current_category is not None:
            previous_page = discord.ui.Button(label="◀", row=1, disabled=self.current_page == 0)
            next_page = discord.ui.Button(label="▶", row=1, disabled=self.current_page >= self._pages() - 1)
            overview = discord.ui.Button(label="All categories", row=1)
            previous_page.callback = self._previous
            next_page.callback = self._next
            overview.callback = self._overview
            for item in (previous_page, next_page, overview):
                self.add_item(item)

    def get_category_embed(self, category: CommandCategory) -> discord.Embed:
        entries = self.categories.get(category) or []
        name, description = category.value
        embed = discord.Embed(title=f"{name} Commands", description=description, color=discord.Color.blue())
        start = self.current_page * PAGE_SIZE
        for cmd_name, cmd_description in entries[start:start + PAGE_SIZE]:
            text = cmd_description if len(cmd_description) <= 100 else cmd_description[:97] + "..."
            embed.add_field(name=f"`/{cmd_name}`", value=text or "No description", inline=False)
        embed.set_footer(text=f"Page {self.current_page + 1}/{self._pages()} · /help command:<name> for details")
        return embed

    def current_embed(self) -> discord.Embed:
        if self.current_category is None:
            return overview_embed(self.categories)
        return self.get_category_embed(self.current_category)

    async def _redraw(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _choose(self, interaction: discord.Interaction) -> None:
        self.current_category = CommandCategory[interaction.data["values"][0]]
        self.current_page = 0
        await self._redraw(interaction)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.current_page = max(0, self.current_page - 1)
        await self._redraw(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.current_page = min(self._pages() - 1, self.current_page + 1)
        await self._redraw(interaction)

    async def _overview(self, interaction: discord.Interaction) -> None:
        self.current_category = None
        self.current_page = 0
        await self._redraw(interaction)


ADMIN_GUIDE: List[Tuple[str, str]] = [
    (
        "1. Settings",
        "`/dashboard` → **Settings**: admin role, league name and the transactions, trading and announcements channels.",
    ),
    (
        "2. Divisions",
        "**Divisions** → **Add division** for each tier (tier 1 is the top). In each division set **Rules** "
        "(scoring, games per matchup, draft on/off), optional **Groups**, the **Championship** playoff (tier 1) "
        "and **Pro/rel below** for the boundary with the next tier.",
    ),
    (
        "3. Teams",
        "**Teams** → **Add team**, then open the team to set its owner, colour and logo. A member owns one team.",
    ),
    (
        "4. Season",
        "**Season** → **Create season**. It starts in setup: put every team in a division (and group) from its "
        "team page and seed draft picks under **Transactions → Draft picks**. Then start the regular season.",
    ),
    (
        "5. Playing the season",
        "Owners report games with `/report-score` or `/report-stats-bulk`. Standings update after every game.",
    ),
    (
        "6. Postseason and next season",
        "End the regular season to lock standings and create the playoff brackets. Playoff games are reported the "
        "same way and attach to their series. When every bracket is done, review the promotions and relegations "
        "and start the next season: teams move divisions and rosters carry over.",
    ),
    (
        "Shortcuts",
        "`/admin-transactions` opens **Transactions** · `/configure-server` opens **Settings** · "
        "`/admin-force-sign`, `/admin-force-release`, `/admin-force-pick` for quick fixes.",
    ),
]


def admin_guide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="⚙️ Running the league",
        description="Everything is done from `/dashboard`. A new league goes through these steps once:",
        color=discord.Color.blue(),
    )
    for name, value in ADMIN_GUIDE:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="Every admin action is recorded in the audit log (Transactions → Audit).")
    return embed


class HelpCog(commands.Cog):
    """Command browser and admin guide."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def categorize_commands(self) -> Dict[CommandCategory, List[Tuple[str, str]]]:
        return categorize(self.bot.tree)

    async def autocomplete_command(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        needle = (current or "").lower()
        names = sorted({cmd.qualified_name for cmd in self.bot.tree.walk_commands()})
        return [app_commands.Choice(name=f"/{name}", value=name) for name in names if needle in name.lower()][:25]

    @app_commands.command(name="help", description="[HELP] Browse commands by category or get details on one command")
    @app_commands.describe(command="Explain one command", search="Find commands by keyword")
    @app_commands.autocomplete(command=autocomplete_command)
    async def help(self, interaction: discord.Interaction, command: Optional[str] = None, search: Optional[str] = None):
        if command:
            await interaction.response.send_message(embed=self._command_embed(command), ephemeral=True)
            return
        if search:
            await interaction.response.send_message(embed=self._search_embed(search), ephemeral=True)
            return
        view = HelpView(self.bot, self.categorize_commands())
        await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)

    def _command_embed(self, name: str) -> discord.Embed:
        wanted = name.strip().lstrip("/").lower()
        target = next((cmd for cmd in self.bot.tree.walk_commands() if cmd.qualified_name.lower() == wanted), None)
        if not target:
            return discord.Embed(
                title="❌ Command not found",
                description=f"There is no `/{wanted}`. Use `/help` to browse commands.",
                color=discord.Color.red(),
            )
        embed = discord.Embed(
            title=f"📖 /{target.qualified_name}",
            description=strip_tag(target.description or "No description"),
            color=discord.Color.green(),
        )
        options = getattr(target, "parameters", None) or []
        if options:
            lines = [
                f"**{opt.display_name}**{'' if opt.required else ' (optional)'}: {opt.description or '…'}"
                for opt in options
            ]
            embed.add_field(name="Options", value="\n".join(lines)[:1024], inline=False)
        return embed

    def _search_embed(self, search: str) -> discord.Embed:
        needle = search.lower()
        matches = sorted(
            (cmd.qualified_name, strip_tag(cmd.description or ""))
            for cmd in self.bot.tree.walk_commands()
            if needle in cmd.qualified_name.lower() or needle in (cmd.description or "").lower()
        )
        if not matches:
            return discord.Embed(
                title="🔍 No matching commands",
                description=f"Nothing matches `{search}`.",
                color=discord.Color.orange(),
            )
        embed = discord.Embed(
            title=f"🔍 Commands matching “{search}”",
            description=f"{len(matches)} found",
            color=discord.Color.blue(),
        )
        for name, description in matches[:10]:
            embed.add_field(name=f"`/{name}`", value=(description or "No description")[:100], inline=False)
        if len(matches) > 10:
            embed.set_footer(text=f"…and {len(matches) - 10} more")
        return embed

    @app_commands.command(name="admin-help", description="[HELP] How to set up and run the league from the dashboard")
    async def admin_help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=admin_guide_embed(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
