"""The public league board: a channel of messages that keep themselves up to date.

Set the channel in ``/dashboard`` → **Settings** (Board). After a result, a void or a league change
the board redraws itself about 10 seconds later, editing its messages in place. If the
messages were deleted, or the board's layout changed (a new division, the playoffs starting), it
clears its old messages and posts a fresh set. On startup it checks the board the same way.

Playoff brackets being set, finishing, and the season ending are also announced in the
announcements channel (once each).

Message ids and sent announcements live under ``board_state`` in the guild's ``bot_config`` row.
Only this cog writes that key (``ConfigUtility.save_guild_config`` leaves it alone).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

from db import get_supabase, queries
from db.league_repository import LeagueRepository
from utils import board_events, branding
from utils.config_manager import get_config_manager
from utils.config_utility import BOARD_STATE_KEY, ConfigUtility
from utils.league_cache import LeagueCache
from utils.standings_image_generator import generate_simple_standings_image
from utils.render.limit import render_in_thread

from cogs.dashboard.playoffs import bracket_image

from .render import BoardData, BoardPost, board_posts, bracket_image_key, pending_announcements, shows_brackets

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 10.0
STARTUP_DELAY = 5.0


def board_channel_id(config: Dict[str, Any]) -> Optional[str]:
    value = (config.get("board") or {}).get("channel_id") if isinstance(config.get("board"), dict) else None
    return str(value) if value else None


async def _resolve_channel(bot: Any, channel_id: Optional[str]) -> Optional[discord.abc.Messageable]:
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel


async def _delete_messages(bot: Any, channel_id: Optional[str], message_ids: List[int]) -> None:
    channel = await _resolve_channel(bot, channel_id)
    if channel is None:
        return
    for message_id in message_ids:
        try:
            await channel.get_partial_message(int(message_id)).delete()
        except discord.HTTPException:
            pass


async def sync_posts(bot: Any, channel_id: Optional[str], posts: List[BoardPost], state: Dict[str, Any]) -> Dict[str, Any]:
    """Make the channel show ``posts``. Returns the new board state (channel, message ids, order)."""
    state = dict(state)
    old_channel = state.get("channel_id")
    old_messages: Dict[str, int] = dict(state.get("messages") or {})
    keys = [post.key for post in posts]

    channel = await _resolve_channel(bot, channel_id)
    if channel is None:
        if old_messages:
            await _delete_messages(bot, old_channel, list(old_messages.values()))
        state.update(channel_id=None, messages={}, order=[])
        return state

    if old_channel == channel_id and state.get("order") == keys and set(old_messages) == set(keys):
        try:
            for post in posts:
                file = post.file()
                await channel.get_partial_message(int(old_messages[post.key])).edit(
                    content=None, embed=post.embed, attachments=[file] if file else []
                )
            return state
        except discord.NotFound:
            logger.info("Board message missing; reposting the board")

    await _delete_messages(bot, old_channel, list(old_messages.values()))
    messages: Dict[str, int] = {}
    for post in posts:
        file = post.file()
        sent = await channel.send(embed=post.embed, file=file) if file else await channel.send(embed=post.embed)
        messages[post.key] = sent.id
    state.update(channel_id=channel_id, messages=messages, order=keys)
    return state


class BoardService:
    """Debounced board refresh for every guild the bot is in."""

    def __init__(self, bot: Any, supabase: Any = None, debounce: float = DEBOUNCE_SECONDS) -> None:
        self.bot = bot
        self.supabase = supabase or get_supabase()
        self.debounce = debounce
        self._task: Optional[asyncio.Task] = None
        self._dirty = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ scheduling
    def schedule(self, delay: Optional[float] = None) -> None:
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(self.debounce if delay is None else delay))

    async def _run(self, delay: float) -> None:
        while self._dirty:
            await asyncio.sleep(delay)
            self._dirty = False
            try:
                await self.refresh_all()
            except Exception as exc:
                logger.error("League board refresh failed: %s", exc, exc_info=True)
            delay = self.debounce

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------ data
    async def collect(self) -> BoardData:
        await LeagueCache.ensure_ready()
        season = LeagueCache.active_season()
        data = BoardData(league_name=branding.get_league_name(), season=season)
        if not season:
            return data
        repo = LeagueRepository(self.supabase)
        data.standings = await queries.get_standings(self.supabase, season["id"])
        if season.get("phase") in ("postseason", "offseason", "completed"):
            data.brackets = await repo.list_brackets(season["id"])
        if season.get("champion_team_id"):
            data.movements = await repo.list_movements(season["id"])
        return data

    async def draw_images(self, data: BoardData) -> Dict[str, bytes]:
        images: Dict[str, bytes] = {}
        if not data.season:
            return images
        for division_id, rows in data.by_division().items():
            division = LeagueCache.division(division_id) or {}
            try:
                image = await render_in_thread(
                    generate_simple_standings_image,
                    rows,
                    data.season.get("name", ""),
                    division.get("name") or division.get("code") or "Division",
                    league_code=division.get("code"),
                    accent_color=division.get("color"),
                )
            except Exception as exc:
                logger.warning("Standings image failed for %s: %s", division.get("code"), exc)
                image = None
            if image is not None:
                images[division_id] = image.getvalue()
        if shows_brackets(data):
            for bracket in data.brackets:
                try:
                    picture = await render_in_thread(bracket_image, bracket, data.season.get("name"))
                except Exception as exc:
                    logger.warning("Bracket image failed for %s: %s", bracket.get("id"), exc)
                    picture = None
                if picture is not None:
                    images[bracket_image_key(bracket)] = picture
        return images

    # ------------------------------------------------------------------ refresh
    async def refresh_all(self) -> None:
        async with self._lock:
            data = await self.collect()
            images: Optional[Dict[str, bytes]] = None
            for guild in list(getattr(self.bot, "guilds", []) or []):
                config = await ConfigUtility.get_guild_config(self.bot, guild.id)
                if board_channel_id(config) and images is None:
                    images = await self.draw_images(data)
                await self.refresh_guild(guild.id, config, data, images or {})

    async def refresh_guild(self, guild_id: int, config: Dict[str, Any], data: BoardData, images: Dict[str, bytes]) -> None:
        manager = get_config_manager()
        guild_config = await manager.get_guild_config(guild_id)
        state: Dict[str, Any] = dict(guild_config.get(BOARD_STATE_KEY) or {})
        before = repr(state)

        channel_id = board_channel_id(config)
        if channel_id or state.get("messages"):
            try:
                state = await sync_posts(self.bot, channel_id, board_posts(data, images) if channel_id else [], state)
            except discord.HTTPException as exc:
                logger.warning("Could not update the league board in guild %s: %s", guild_id, exc)

        state = await self.announce(config, data, state)
        if repr(state) != before:
            guild_config.set(BOARD_STATE_KEY, state)
            await guild_config.save()

    async def announce(self, config: Dict[str, Any], data: BoardData, state: Dict[str, Any]) -> Dict[str, Any]:
        """Send announcements not sent yet. The first run only records what already happened."""
        first_run = "announced" not in state
        announced = set(state.get("announced") or [])
        pending = pending_announcements(data, announced)
        if not pending and not first_run:
            return state
        channel = None if first_run else await _resolve_channel(self.bot, config.get("announcements_channel_id"))
        for key, embed in pending:
            if channel is not None:
                try:
                    await channel.send(embed=embed)
                except discord.HTTPException as exc:
                    logger.warning("Could not send announcement %s: %s", key, exc)
                    continue
            announced.add(key)
        state = dict(state)
        state["announced"] = sorted(announced)
        return state


class BoardCog(commands.Cog):
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.service = BoardService(bot)
        board_events.subscribe(self.service.schedule)

    def cog_unload(self) -> None:
        board_events.unsubscribe(self.service.schedule)
        self.service.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.service.schedule(STARTUP_DELAY)


async def setup(bot: Any) -> None:
    await bot.add_cog(BoardCog(bot))
