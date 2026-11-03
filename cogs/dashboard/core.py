"""Building blocks for the admin dashboard.

The dashboard is one ephemeral message. Each screen is a ``DashboardPage`` (a discord View). Opening
a screen pushes it on the session's stack and edits the same message in place, so **Back** really goes
back. Pages rebuild their components on every render, which keeps buttons in sync with the data.

Changes go through ``DashboardPage.apply``. It runs the change, shows rule violations
(``LeagueRuleError``) as a notice on the page, refreshes the caches and re-renders.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Optional

import discord

from db.league_repository import LeagueRepository, LeagueRuleError, friendly_error
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x2B2D31
PAGE_TIMEOUT = 900  # seconds of inactivity before the buttons stop working


class DashboardSession:
    """State shared by every page of one open dashboard."""

    def __init__(self, bot: Any, guild_id: int, user_id: int, supabase: Any) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.supabase = supabase
        self.repo = LeagueRepository(supabase)
        self.stack: List["DashboardPage"] = []

    @property
    def guild(self) -> Optional[discord.Guild]:
        return self.bot.get_guild(self.guild_id) if self.bot else None


async def refresh_caches(*, rosters: bool = False) -> None:
    """Reload league structure after a change (and the score-report roster cache when asked)."""
    from utils.team_player_cache import TeamPlayerCache

    await TeamPlayerCache.refresh()  # also refreshes LeagueCache
    if rosters:
        from cogs.score.roster_cache import RosterCache

        RosterCache().invalidate()


class DashboardPage(discord.ui.View):
    """One dashboard screen."""

    title = "Dashboard"

    def __init__(self, session: DashboardSession) -> None:
        super().__init__(timeout=PAGE_TIMEOUT)
        self.session = session
        self.notice: Optional[str] = None

    # ------------------------------------------------------------------ to override
    async def load(self) -> None:
        """Fetch what the page shows. Runs before every render."""

    def build(self) -> None:
        """Add the page's components (the nav row is added afterwards)."""

    def embed(self) -> discord.Embed:
        return discord.Embed(title=self.title, color=EMBED_COLOR)

    # ------------------------------------------------------------------ rendering
    @property
    def repo(self) -> LeagueRepository:
        return self.session.repo

    async def render(self) -> discord.Embed:
        await LeagueCache.ensure_ready()
        await self.load()
        self.clear_items()
        self.build()
        self._add_nav()
        embed = self.embed()
        if self.notice:
            embed.description = f"{self.notice}\n\n{embed.description or ''}".strip()
            self.notice = None
        return embed

    def _add_nav(self) -> None:
        depth = len(self.session.stack)
        if depth > 1:
            self.button("Back", self._back, emoji="🔙", row=4)
        if depth > 2:
            self.button("Home", self._home, emoji="🏠", row=4)

    async def show(self, interaction: discord.Interaction) -> None:
        """Render this page into the dashboard message."""
        embed = await self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def open(self, interaction: discord.Interaction, page: "DashboardPage") -> None:
        self.session.stack.append(page)
        await page.show(interaction)

    async def _back(self, interaction: discord.Interaction) -> None:
        if len(self.session.stack) > 1:
            self.session.stack.pop()
        await self.session.stack[-1].show(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        del self.session.stack[1:]
        await self.session.stack[0].show(interaction)

    # ------------------------------------------------------------------ helpers
    def button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[Any]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: Optional[str] = None,
        row: Optional[int] = None,
        disabled: bool = False,
    ) -> discord.ui.Button:
        item = discord.ui.Button(label=label, style=style, emoji=emoji, row=row, disabled=disabled)
        item.callback = callback
        self.add_item(item)
        return item

    def select(
        self,
        placeholder: str,
        options: List[discord.SelectOption],
        callback: Callable[[discord.Interaction, str], Awaitable[Any]],
        *,
        row: Optional[int] = None,
    ) -> discord.ui.Select:
        """Single-choice select; ``callback`` receives the chosen value."""
        if not options:
            options = [discord.SelectOption(label="Nothing to choose", value="__none")]
        item = discord.ui.Select(placeholder=placeholder, options=options[:25], row=row, disabled=options[0].value == "__none")

        async def handler(interaction: discord.Interaction) -> None:
            await callback(interaction, item.values[0])

        item.callback = handler
        self.add_item(item)
        return item

    async def apply(
        self,
        interaction: discord.Interaction,
        change: Callable[[], Awaitable[Any]],
        success: str,
        *,
        rosters: bool = False,
        page: Optional["DashboardPage"] = None,
    ) -> bool:
        """Run a change, refresh caches and re-render ``page`` (default: this page) with a notice."""
        target = page or self
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            await change()
        except LeagueRuleError as exc:
            target.notice = f"❌ {exc.message}"
            await target.show(interaction)
            return False
        except Exception as exc:
            logger.error("Dashboard change failed: %s", exc, exc_info=True)
            target.notice = f"❌ {friendly_error(exc, 'Something went wrong. Check the bot logs.')}"
            await target.show(interaction)
            return False
        await refresh_caches(rosters=rosters)
        target.notice = f"✅ {success}"
        await target.show(interaction)
        return True

    # ------------------------------------------------------------------ discord hooks
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message("❌ This dashboard belongs to someone else. Open your own with `/dashboard`.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error("Dashboard error in %s: %s", type(self).__name__, error, exc_info=error)
        message = f"❌ {friendly_error(error, 'Something went wrong. Check the bot logs.')}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class FormModal(discord.ui.Modal):
    """Modal that hands its submit interaction to a callback (the page then edits the message)."""

    def __init__(self, title: str, on_submit: Callable[[discord.Interaction, "FormModal"], Awaitable[Any]]) -> None:
        super().__init__(title=title[:45])
        self._on_submit = on_submit

    def text(
        self,
        label: str,
        *,
        default: Optional[str] = None,
        placeholder: Optional[str] = None,
        required: bool = True,
        max_length: int = 100,
        style: discord.TextStyle = discord.TextStyle.short,
    ) -> discord.ui.TextInput:
        field = discord.ui.TextInput(
            label=label[:45],
            default=(default or None),
            placeholder=placeholder,
            required=required,
            max_length=max_length,
            style=style,
        )
        self.add_item(field)
        return field

    def choice(
        self,
        label: str,
        options: List[discord.SelectOption],
        *,
        description: Optional[str] = None,
    ) -> discord.ui.Select:
        """A select inside the modal (wrapped in a Label)."""
        select = discord.ui.Select(options=options[:25], min_values=1, max_values=1)
        self.add_item(discord.ui.Label(text=label[:45], description=description, component=select))
        return select

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, self)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error("Dashboard form error: %s", error, exc_info=error)
        message = f"❌ {friendly_error(error, 'Something went wrong. Check the bot logs.')}"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def parse_int(raw: Optional[str], label: str, *, minimum: int, maximum: int) -> int:
    """Parse a whole number from a form, raising LeagueRuleError with a readable message."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise LeagueRuleError("INVALID", f"{label} must be a whole number.")
    if not minimum <= value <= maximum:
        raise LeagueRuleError("INVALID", f"{label} must be between {minimum} and {maximum}.")
    return value


def parse_color(raw: Optional[str]) -> Optional[str]:
    """``#RRGGBB`` from a form ("" → None)."""
    text = (raw or "").strip().lstrip("#")
    if not text:
        return None
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise LeagueRuleError("INVALID", "Colours must be hex like #1F8B4C.")
    return f"#{text.upper()}"


def division_label(division: Optional[dict]) -> str:
    if not division:
        return "No division"
    code = (division.get("code") or "").upper()
    name = division.get("name") or code
    emoji = f"{division['emoji']} " if division.get("emoji") else ""
    return f"{emoji}{code} · {name}" if name and name.upper() != code else f"{emoji}{name or code}"


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
