"""Tests for the public league board (rendering, message sync, announcements, debounce)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord
import pytest

from cogs.board import board as board_module
from cogs.board.board import BoardService, board_channel_id, sync_posts
from cogs.board.render import BoardData, BoardPost, board_posts, overview_embed, pending_announcements
from utils import board_events
from utils.league_cache import LeagueCache

TEAMS = {"a": "Ants", "b": "Bees", "c": "Cats", "x": "Xrays", "y": "Yaks"}


@pytest.fixture(autouse=True)
def league():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "Season 1", "active": True, "phase": "regular"}],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "color": "#ff0000", "settings": {}},
            {"id": "d2", "code": "D2", "name": "Second", "tier": 2, "settings": {}},
        ],
    )
    with patch("utils.team_player_cache.TeamPlayerCache.get_team", side_effect=lambda t: {"id": t, "name": TEAMS.get(t, "?")}):
        yield
    LeagueCache.invalidate()


def row(team, division, rank, wins=0, losses=0, zone=None):
    return {"team_id": team, "team_name": TEAMS[team], "division_id": division, "rank": rank,
            "wins": wins, "losses": losses, "point_diff": wins - losses, "zone": zone}


def data(phase="regular", **kwargs):
    season = {"id": "s1", "name": "Season 1", "phase": phase, **kwargs.pop("season", {})}
    standings = [
        row("x", "d2", 1, 3, 0, "promoted"), row("y", "d2", 2),
        row("b", "d1", 2, 1, 2), row("a", "d1", 1, 3, 0, "champion_playoff"), row("c", "d1", 3, 0, 3, "relegated"),
    ]
    return BoardData(league_name="Bobble League", season=season, standings=standings, **kwargs)


def bracket(status="in_progress", winner=None):
    final = {"id": "f", "round": 1, "position": 0, "team1_id": "a", "team2_id": "b", "winner_id": winner,
             "loser_id": "b" if winner else None, "winner_to": None, "status": "completed" if winner else "ready"}
    return {"id": "br1", "kind": "championship", "division_id": "d1", "status": status,
            "series": [final], "bracket_entries": [{"team_id": "a", "seed": 1}, {"team_id": "b", "seed": 2}]}


# ------------------------------------------------------------------------------------ rendering
def test_posts_are_overview_then_divisions_by_tier():
    posts = board_posts(data(), images={"d1": b"png"})
    assert [p.key for p in posts] == ["overview", "division:d1", "division:d2"]
    d1, d2 = posts[1], posts[2]
    assert d1.filename == "standings-d1.png" and d1.embed.image.url == "attachment://standings-d1.png"
    assert d1.embed.color.value == 0xFF0000
    assert d2.image is None and "Xrays" in d2.embed.description  # text table when no image


def test_overview_shows_phase_leaders_and_zones():
    embed = overview_embed(data(season={"champion_team_id": "a"}))
    assert embed.title == "🏟️ Bobble League"
    assert "Regular season" in embed.description and "Champion: **Ants**" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    d1_field = next(v for k, v in fields.items() if "Premier" in k)
    assert d1_field.startswith("1. **Ants** 3-0 (+3)")
    assert "Championship playoff" in fields["Zones"] and "Relegation" in fields["Zones"]


def test_overview_without_a_season():
    posts = board_posts(BoardData(league_name="L", season=None))
    assert [p.key for p in posts] == ["overview"]
    assert "No season yet" in posts[0].embed.description


def test_brackets_appear_in_the_postseason_as_text_without_an_image():
    posts = board_posts(data("postseason", brackets=[bracket()]))
    assert posts[-1].key == "bracket:br1"
    assert posts[-1].image is None and "Ants" in posts[-1].embed.description
    assert "bracket:br1" not in [p.key for p in board_posts(data("regular", brackets=[bracket()]))]


def test_a_bracket_post_carries_its_image():
    posts = board_posts(data("postseason", brackets=[bracket()]), images={"bracket:br1": b"png"})
    post = posts[-1]
    assert post.image == b"png" and post.filename == "bracket-championship-d1.png"
    assert post.embed.image.url == "attachment://bracket-championship-d1.png"
    assert post.embed.title.startswith("Championship ·")
    assert post.embed.description is None


def test_post_file_is_fresh_each_time():
    post = BoardPost(key="k", embed=discord.Embed(), image=b"abc", filename="x.png")
    assert post.file() is not post.file()


# ------------------------------------------------------------------------------- announcements
def test_announcements_for_playoffs_brackets_and_season_end():
    done = data("offseason", season={"champion_team_id": "a"}, brackets=[bracket("completed", winner="a")],
                movements=[{"team_id": "c", "from_division_id": "d1", "to_division_id": "d2", "reason": "relegated"}])
    keys = [key for key, _ in pending_announcements(done, set())]
    assert keys == ["postseason:s1", "bracket:br1", "champion:s1"]
    embeds = dict(pending_announcements(done, set()))
    assert "**Ants** win" in embeds["bracket:br1"].description
    assert "Cats" in embeds["champion:s1"].fields[0].value and "D1 → D2" in embeds["champion:s1"].fields[0].value
    assert pending_announcements(done, {"postseason:s1", "bracket:br1", "champion:s1"}) == []


def test_nothing_to_announce_in_the_regular_season():
    assert pending_announcements(data(), set()) == []


# ------------------------------------------------------------------------------------- syncing
class FakeChannel:
    def __init__(self):
        self.sent = []
        self.partials = {}
        self.missing = set()
        self.next_id = 100

    async def send(self, embed=None, file=None):
        self.next_id += 1
        self.sent.append((embed, file))
        return SimpleNamespace(id=self.next_id)

    def get_partial_message(self, message_id):
        partial = self.partials.setdefault(message_id, Mock())
        if message_id in self.missing:
            partial.edit = AsyncMock(side_effect=discord.NotFound(Mock(status=404), "gone"))
        else:
            partial.edit = partial.edit if isinstance(partial.edit, AsyncMock) else AsyncMock()
        partial.delete = partial.delete if isinstance(partial.delete, AsyncMock) else AsyncMock()
        return partial


def posts(*keys):
    return [BoardPost(key=k, embed=discord.Embed(title=k)) for k in keys]


def bot_with(channel):
    return SimpleNamespace(get_channel=lambda _id: channel, fetch_channel=AsyncMock())


async def test_first_sync_posts_every_message():
    channel = FakeChannel()
    state = await sync_posts(bot_with(channel), "1", posts("overview", "division:d1"), {})
    assert len(channel.sent) == 2
    assert state == {"channel_id": "1", "messages": {"overview": 101, "division:d1": 102}, "order": ["overview", "division:d1"]}


async def test_same_layout_edits_in_place():
    channel = FakeChannel()
    state = {"channel_id": "1", "messages": {"overview": 5, "division:d1": 6}, "order": ["overview", "division:d1"]}
    new_state = await sync_posts(bot_with(channel), "1", posts("overview", "division:d1"), state)
    assert channel.sent == []
    channel.partials[5].edit.assert_awaited_once()
    assert new_state == state


async def test_layout_change_reposts_and_cleans_up():
    channel = FakeChannel()
    state = {"channel_id": "1", "messages": {"overview": 5}, "order": ["overview"]}
    new_state = await sync_posts(bot_with(channel), "1", posts("overview", "division:d1"), state)
    channel.partials[5].delete.assert_awaited_once()
    assert len(channel.sent) == 2 and new_state["order"] == ["overview", "division:d1"]


async def test_deleted_message_triggers_a_repost():
    channel = FakeChannel()
    channel.missing.add(5)
    state = {"channel_id": "1", "messages": {"overview": 5}, "order": ["overview"]}
    new_state = await sync_posts(bot_with(channel), "1", posts("overview"), state)
    assert len(channel.sent) == 1 and new_state["messages"] == {"overview": 101}


async def test_clearing_the_channel_removes_the_board():
    channel = FakeChannel()
    state = {"channel_id": "1", "messages": {"overview": 5}, "order": ["overview"]}
    new_state = await sync_posts(bot_with(channel), None, [], state)
    assert new_state == {"channel_id": None, "messages": {}, "order": []}


def test_board_channel_from_config():
    assert board_channel_id({"board": {"channel_id": 12}}) == "12"
    assert board_channel_id({"board": "junk"}) is None
    assert board_channel_id({}) is None


# --------------------------------------------------------------------------- service behaviour
def service():
    return BoardService(SimpleNamespace(guilds=[]), supabase=Mock(), debounce=0.01)


async def test_burst_of_changes_refreshes_once():
    svc = service()
    svc.refresh_all = AsyncMock()
    board_events.subscribe(svc.schedule)
    try:
        for _ in range(5):
            board_events.league_changed()
        await asyncio.sleep(0.05)
    finally:
        board_events.unsubscribe(svc.schedule)
    svc.refresh_all.assert_awaited_once()


async def test_first_announcement_run_only_records_history():
    svc = service()
    done = data("offseason", season={"champion_team_id": "a"}, brackets=[bracket("completed", winner="a")])
    with patch.object(board_module, "_resolve_channel", AsyncMock()) as resolve:
        state = await svc.announce({"announcements_channel_id": "9"}, done, {})
    resolve.assert_not_awaited()
    assert state["announced"] == ["bracket:br1", "champion:s1", "postseason:s1"]


async def test_new_results_are_announced_once():
    svc = service()
    channel = FakeChannel()
    done = data("postseason", brackets=[bracket("completed", winner="a")])
    with patch.object(board_module, "_resolve_channel", AsyncMock(return_value=channel)):
        state = await svc.announce({"announcements_channel_id": "9"}, done, {"announced": ["postseason:s1"]})
        assert len(channel.sent) == 1
        await svc.announce({"announcements_channel_id": "9"}, done, state)
    assert len(channel.sent) == 1


async def test_refresh_guild_saves_state_only_when_it_changes():
    svc = service()
    guild_config = MagicMock()
    guild_config.get.return_value = {"announced": []}
    guild_config.save = AsyncMock()
    manager = Mock(get_guild_config=AsyncMock(return_value=guild_config))
    with patch.object(board_module, "get_config_manager", return_value=manager):
        await svc.refresh_guild(1, {}, data(), {})
    guild_config.save.assert_not_awaited()


async def test_settings_save_keeps_the_boards_state():
    from utils.config_utility import ConfigUtility

    stored = {"messages": {"overview": 1}}
    guild_config = SimpleNamespace(_cache={}, save=AsyncMock(), get=lambda key: stored if key == "board_state" else None)
    manager = Mock(get_guild_config=AsyncMock(return_value=guild_config))
    with patch("utils.config_utility.get_config_manager", return_value=manager):
        await ConfigUtility.save_guild_config(SimpleNamespace(), 1, {"league_name": "X", "board_state": {"messages": {}}})
    assert guild_config._cache["board_state"] == stored
    assert guild_config._cache["league_name"] == "X"
