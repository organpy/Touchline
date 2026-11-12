"""Tests for the admin dashboard (navigation, gating and forms)."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
from PIL import Image

from cogs.dashboard import core, dashboard
from cogs.dashboard.core import DashboardSession, parse_color, parse_int
from cogs.dashboard.divisions import DivisionPage, DivisionsPage, parse_best_of_list, parse_pair
from cogs.dashboard.home import HomePage
from cogs.dashboard.teams import TeamPage
from db.league_repository import LeagueRuleError
from utils.league_cache import LeagueCache
from utils.logo_store import LogoError, normalize_logo


@pytest.fixture(autouse=True)
def league():
    LeagueCache.load_snapshot(
        seasons=[{"id": "s1", "number": 1, "name": "Season 1", "active": True, "phase": "setup"}],
        divisions=[
            {"id": "d1", "code": "D1", "name": "Premier", "tier": 1, "settings": {}},
            {"id": "d2", "code": "D2", "name": "Championship", "tier": 2, "settings": {"picks_enabled": True}},
        ],
        groups=[{"id": "g1", "division_id": "d1", "name": "East", "sort_order": 0}],
        assignments={"s1": [{"team_id": "t1", "division_id": "d1", "group_id": "g1"}]},
    )
    with patch.object(core, "refresh_caches", AsyncMock()), patch(
        "utils.team_player_cache.TeamPlayerCache.get_all_teams",
        return_value=[{"id": "t1", "code": "pan", "name": "Pandas"}, {"id": "t2", "code": "owl", "name": "Owls"}],
    ):
        yield
    LeagueCache.invalidate()


def make_session(user_id=1):
    session = DashboardSession(Mock(), 99, user_id, Mock())
    session.repo = AsyncMock()
    return session


def make_interaction(user_id=1):
    interaction = Mock()
    interaction.user = Mock(id=user_id)
    interaction.response = Mock()
    interaction.response.is_done = Mock(return_value=False)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock(side_effect=lambda *a, **k: interaction.response.is_done.configure_mock(return_value=True))
    interaction.edit_original_response = AsyncMock()
    return interaction


def labels(page):
    return [getattr(item, "label", None) for item in page.children]


@pytest.mark.asyncio
async def test_home_shows_divisions_and_next_steps():
    session = make_session()
    home = HomePage(session)
    session.stack.append(home)
    embed = await home.render()
    names = [f.name for f in embed.fields]
    assert any("D1 · Premier" in n for n in names)
    steps = next(f.value for f in embed.fields if f.name == "Next steps")
    assert "1 team(s) still need a division" in steps  # Owls has no division
    assert "Back" not in labels(home)


@pytest.mark.asyncio
async def test_navigation_back_and_home():
    session = make_session()
    home = HomePage(session)
    session.stack.append(home)
    await home.render()

    divisions = DivisionsPage(session)
    await home.open(make_interaction(), divisions)
    assert "Back" in labels(divisions) and "Home" not in labels(divisions)

    division = DivisionPage(session, "d1")
    await divisions.open(make_interaction(), division)
    assert "Home" in labels(division)

    await division._home(make_interaction())
    assert session.stack == [home]


@pytest.mark.asyncio
async def test_other_users_cannot_drive_the_dashboard():
    page = HomePage(make_session(user_id=1))
    intruder = make_interaction(user_id=2)
    assert await page.interaction_check(intruder) is False
    intruder.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_division_buttons_follow_tier():
    session = make_session()
    top = DivisionPage(session, "d1")
    session.stack.extend([HomePage(session), top])
    await top.render()
    buttons = {item.label: item for item in top.children if isinstance(item, discord.ui.Button)}
    assert not buttons["Championship"].disabled
    assert not buttons["Pro/rel below"].disabled
    assert buttons["Move up"].disabled

    bottom = DivisionPage(session, "d2")
    await bottom.render()
    buttons = {item.label: item for item in bottom.children if isinstance(item, discord.ui.Button)}
    assert buttons["Championship"].disabled
    assert buttons["Pro/rel below"].disabled


@pytest.mark.asyncio
async def test_rules_form_saves_validated_settings():
    session = make_session()
    page = DivisionPage(session, "d2")
    session.stack.extend([HomePage(session), page])
    await page.render()
    modal = SimpleNamespace(
        scoring=SimpleNamespace(values=["win_points"]),
        points=SimpleNamespace(value="2"),
        games=SimpleNamespace(value="3"),
        picks=SimpleNamespace(values=["off"]),
    )
    interaction = make_interaction()
    await page._submit_rules(interaction, modal)

    division_id, saved = session.repo.update_division_settings.await_args.args
    assert division_id == "d2"
    assert saved["scoring_mode"] == "win_points"
    assert saved["points_per_win"] == 2
    assert saved["games_per_matchup"] == 3
    assert saved["picks_enabled"] is False
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.description.startswith("✅")


@pytest.mark.asyncio
async def test_rules_form_reports_bad_numbers_on_the_page():
    session = make_session()
    page = DivisionPage(session, "d2")
    session.stack.extend([HomePage(session), page])
    modal = SimpleNamespace(
        scoring=SimpleNamespace(values=["points_scored"]),
        points=SimpleNamespace(value="3"),
        games=SimpleNamespace(value="50"),
        picks=SimpleNamespace(values=["on"]),
    )
    interaction = make_interaction()
    await page._submit_rules(interaction, modal)
    session.repo.update_division_settings.assert_not_awaited()
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Games per matchup must be between 1 and 10" in embed.description


@pytest.mark.asyncio
async def test_owner_who_already_owns_a_team_is_refused():
    session = make_session()
    session.repo.get_team.return_value = {"id": "t2", "code": "owl", "name": "Owls"}
    page = TeamPage(session, "t2")
    session.stack.extend([HomePage(session), page])
    user = Mock(id=555, bot=False, mention="<@555>")
    modal = SimpleNamespace(user=SimpleNamespace(values=[user]))
    interaction = make_interaction()
    with patch("cogs.dashboard.teams.TeamPlayerCache.get_team_by_owner", return_value={"id": "t1", "name": "Pandas"}):
        await page._submit_owner(interaction, modal)
    session.repo.update_team.assert_not_awaited()
    assert "already owns **Pandas**" in interaction.edit_original_response.await_args.kwargs["embed"].description


@pytest.mark.asyncio
async def test_team_page_division_select_assigns_for_the_season():
    session = make_session()
    session.repo.get_team.return_value = {"id": "t2", "code": "owl", "name": "Owls"}
    page = TeamPage(session, "t2")
    session.stack.extend([HomePage(session), page])
    await page.render()
    await page._set_division(make_interaction(), "d2")
    session.repo.assign_team.assert_awaited_once_with("s1", "t2", "d2", None)


@pytest.mark.asyncio
async def test_dashboard_requires_admin():
    interaction = make_interaction()
    interaction.guild = Mock(id=99)
    with patch.object(dashboard.ConfigUtility, "get_guild_config", AsyncMock(return_value={})), patch.object(
        dashboard.ConfigUtility, "check_admin_channel_restriction", AsyncMock(return_value=(True, None))
    ), patch.object(dashboard.ConfigUtility, "check_admin_permissions", AsyncMock(return_value=False)), patch.object(
        dashboard.ConfigUtility, "send_permission_denied_message", AsyncMock()
    ) as denied:
        assert await dashboard.open_dashboard(Mock(), interaction) is None
    denied.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def test_form_parsers():
    assert parse_int(" 4 ", "Teams", minimum=2, maximum=16) == 4
    with pytest.raises(LeagueRuleError):
        parse_int("x", "Teams", minimum=2, maximum=16)
    assert parse_color("1f8b4c") == "#1F8B4C"
    assert parse_color("") is None
    with pytest.raises(LeagueRuleError):
        parse_color("#12345")
    assert parse_best_of_list("1, 3/5") == [1, 3, 5]
    assert parse_pair("4 / 8", "Sizes") == (4, 8)
    with pytest.raises(LeagueRuleError):
        parse_pair("4", "Sizes")


def test_logo_normalised_to_square_png():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 400), "red").save(buffer, format="JPEG")
    png = normalize_logo(buffer.getvalue())
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (512, 512)
    with pytest.raises(LogoError):
        normalize_logo(b"not an image")


@pytest.mark.asyncio
async def test_section_shortcut_opens_on_top_of_home():
    from cogs.dashboard.transactions import TransactionsPage

    interaction = make_interaction()
    interaction.guild = Mock(id=99)
    with patch.object(dashboard, "check_dashboard_access", AsyncMock(return_value=True)), patch.object(
        dashboard, "get_supabase", return_value=Mock()
    ):
        session = await dashboard.open_dashboard(Mock(), interaction, TransactionsPage)
    assert [type(p).__name__ for p in session.stack] == ["HomePage", "TransactionsPage"]
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert "Back" in labels(view) and "Rosters" in labels(view)


@pytest.mark.asyncio
async def test_season_page_offers_create_only_without_a_season():
    from cogs.dashboard.season import SeasonPage

    session = make_session()
    page = SeasonPage(session)
    session.stack.extend([HomePage(session), page])
    with patch.object(LeagueCache, "load_season_teams", AsyncMock()):
        embed = await page.render()
    assert "Season 1" in embed.description
    assert "Create season" not in labels(page)

    LeagueCache.load_snapshot(seasons=[], divisions=[])
    await page.render()
    assert "Create season" in labels(page)


def test_settings_channels_read_and_write_nested_keys():
    from cogs.dashboard.settings import read_channel, write_channel

    config = {"trading": "broken"}
    write_channel(config, "trading", "123")
    write_channel(config, "announcements", "456")
    assert config["trading"] == {"channel_id": "123"}
    assert read_channel(config, "trading") == "123"
    assert read_channel(config, "announcements") == "456"
    write_channel(config, "announcements", None)
    assert read_channel(config, "announcements") is None


@pytest.mark.asyncio
async def test_season_step_previews_before_confirming():
    from cogs.dashboard.season import SeasonPage

    session = make_session()
    page = SeasonPage(session)
    session.stack.extend([HomePage(session), page])
    with patch.object(LeagueCache, "load_season_teams", AsyncMock()):
        await page.render()
        assert "Start regular season" in labels(page)
        await page._step(make_interaction())
    # D1 has one team and D2 none, so the step is blocked
    confirm = next(item for item in page.children if getattr(item, "label", "").startswith("Confirm"))
    assert confirm.disabled
    assert "needs at least 2 teams" in page.preview["text"]
    session.repo.set_season_phase.assert_not_awaited()


def test_render_bracket_names_rounds_and_marks_winners():
    from cogs.dashboard.playoffs import bracket_started, render_bracket

    bracket = {
        "kind": "championship",
        "bracket_entries": [{"team_id": "t1", "seed": 1}, {"team_id": "t2", "seed": 2}],
        "series": [{"round": 1, "position": 0, "team1_id": "t1", "team2_id": "t2", "team1_wins": 2,
                    "team2_wins": 1, "winner_id": "t1", "status": "completed", "best_of": 3}],
    }
    with patch("cogs.dashboard.playoffs.TeamPlayerCache.get_team", side_effect=lambda t: {"name": t.upper()}):
        text = render_bracket(bracket)
    assert "__Final__" in text
    assert "(1) **T1** 2–1 (2) T2 · bo3" in text
    assert "✅" not in text
    assert bracket_started(bracket)


async def test_matches_page_voids_after_confirming():
    from cogs.dashboard import matches as matches_module
    from cogs.dashboard.matches import MatchesPage

    session = make_session()
    session.repo.list_recent_matches.return_value = [
        {"id": "m1", "team1_id": "t1", "team2_id": "t2", "team1_score": 3, "team2_score": 1,
         "stage": "regular", "status": "final", "season_id": "s1", "division_id": "d1"},
        {"id": "m0", "team1_id": "t2", "team2_id": "t1", "team1_score": 2, "team2_score": 0,
         "stage": "regular", "status": "void"},
    ]
    page = MatchesPage(session)
    embed = await page.render()
    assert "~~" in embed.description           # voided games are struck through
    select = next(item for item in page.children if isinstance(item, discord.ui.Select))
    assert [o.value for o in select.options] == ["m1"]  # only counted games can be voided
    assert next(i for i in page.children if getattr(i, "label", None) == "Void game").disabled

    interaction = make_interaction()
    await page._pick(interaction, "m1")
    await page._ask(interaction)
    assert "Confirm void" in labels(page)
    with patch.object(matches_module, "void_match", AsyncMock()) as void:
        await page._void(interaction)
    void.assert_awaited_once()
    assert void.await_args.args[1]["id"] == "m1"
