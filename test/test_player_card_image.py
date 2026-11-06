"""The player card: career maths, layout, caching and the command that feeds it."""

import io
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from PIL import Image

from utils import player_card_image_generator as gen
from utils.render import canvas


@pytest.fixture(autouse=True)
def fresh_cache():
    gen.player_card_cache.clear()
    yield
    gen.player_card_cache.clear()


def season(number, team, **stats):
    row = {"season_id": f"s{number}", "season_number": number, "season_name": f"S{number}", "team_name": team,
           "games_played": 10, "goals": 3, "assists": 2, "saves": 5, "blocks": 1, "points": 20}
    row.update(stats)
    return row


CAREER = [season(2, "Falcons", goals=7), season(1, "Titans", goals=4), season(3, "Falcons", goals=1)]


# ----------------------------------------------------------------------------------------- career data


def test_career_totals_add_up_every_season():
    totals = gen.career_totals(CAREER)

    assert totals["goals"] == 12 and totals["games_played"] == 30 and totals["points"] == 60


def test_career_totals_cope_with_missing_values_and_no_seasons():
    assert gen.career_totals([{"goals": None}, {}])["goals"] == 0
    assert gen.career_totals([]) == {key: 0 for _, key in gen.STAT_ROWS}


def test_seasons_played_shows_the_first_and_last_season():
    assert gen.seasons_played(CAREER) == "S1 – S3"
    assert gen.seasons_played([season(4, "Owls")]) == "S4"
    assert gen.seasons_played([]) == ""


def test_teams_played_lists_each_team_once_oldest_first():
    assert gen.teams_played(CAREER) == ["Titans", "Falcons"]
    assert gen.teams_played([season(1, None), season(2, "Owls")]) == ["Owls"]
    assert gen.teams_played([]) == []


# ----------------------------------------------------------------------------------------- image


def open_png(buffer):
    return Image.open(io.BytesIO(buffer.getvalue()))


def test_the_card_is_a_png_of_a_fixed_size():
    full = open_png(gen.generate_player_card_image("Ace", "Falcons", "falcons", "D1", "Season 3", CAREER[2], CAREER))
    empty = open_png(gen.generate_player_card_image("New Player"))

    assert full.format == "PNG" and full.size == empty.size
    assert full.width == gen.WIDTH


def test_the_accent_strip_uses_the_division_colour():
    with patch.object(gen, "accent_color", return_value=(255, 0, 0)):
        image = open_png(gen.generate_player_card_image("Ace", league_code="D1")).convert("RGB")

    assert image.getpixel((gen.WIDTH // 2, canvas.PADDING + 2)) == (255, 0, 0)


def test_odd_input_still_gives_a_card():
    odd_career = [{"season_name": "S1"}, {"team_name": "No Season"}, {}]

    card = gen.generate_player_card_image("A very long display name " * 6, "A very long team name " * 6, None, "d1",
                                          "A very long season name " * 4, {"goals": None, "points": "7"}, odd_career)

    assert card is not None and open_png(card).format == "PNG"


def test_the_same_card_is_rendered_once_and_changed_stats_render_again():
    with patch.object(gen, "_render", wraps=gen._render) as spy:
        first = gen.generate_player_card_image("Ace", "Falcons", "falcons", "D1", "S3", CAREER[2], CAREER)
        second = gen.generate_player_card_image("Ace", "Falcons", "falcons", "D1", "S3", CAREER[2], CAREER)
        gen.generate_player_card_image("Ace", "Falcons", "falcons", "D1", "S3", CAREER[2], CAREER + [season(4, "Owls")])

    assert spy.call_count == 2
    assert first.getvalue() == second.getvalue()


def test_a_drawing_failure_gives_no_card():
    with patch.object(gen, "_render", side_effect=RuntimeError("boom")):
        assert gen.generate_player_card_image("Ace") is None


@pytest.mark.asyncio
async def test_the_async_wrapper_returns_png_bytes_and_none_on_failure():
    data = await gen.generate_player_card(player_name="Ace", team_name="Falcons", career=CAREER)
    assert data.startswith(b"\x89PNG")

    with patch.object(gen, "_render", side_effect=RuntimeError("boom")):
        assert await gen.generate_player_card(player_name="Ace") is None


# ----------------------------------------------------------------------------------------- command


def _interaction():
    interaction = Mock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_the_command_makes_one_career_query_and_sends_just_the_card():
    from cogs.cards import player_card as command_module
    from utils.league_cache import LeagueCache
    from utils.team_player_cache import TeamPlayerCache

    TeamPlayerCache.invalidate()
    LeagueCache.load_snapshot(
        seasons=[{"id": "s3", "number": 3, "name": "S3", "active": True}, {"id": "s2", "number": 2, "name": "S2", "active": False}],
        divisions=[],
    )
    TeamPlayerCache._store_team({"id": "t1", "code": "falcons", "name": "Falcons"})
    TeamPlayerCache._players = {"p1": {"id": "p1", "display_name": "Ace", "current_team_id": "t1"}}
    TeamPlayerCache._players_by_name = {"ace": TeamPlayerCache._players["p1"]}
    cog = command_module.PlayerCardCog.__new__(command_module.PlayerCardCog)
    cog.supabase = MagicMock()
    interaction = _interaction()
    career_query = AsyncMock(return_value=CAREER)
    card = AsyncMock(return_value=b"png-bytes")

    try:
        with patch.object(command_module.queries, "get_player_career", career_query), \
             patch.object(command_module, "generate_player_card", card):
            await command_module.PlayerCardCog.player_card_command.callback(cog, interaction, "Ace", "S3")
    finally:
        TeamPlayerCache.invalidate()
        LeagueCache.invalidate()

    career_query.assert_awaited_once_with(cog.supabase, "p1")
    shown = card.await_args.kwargs
    assert shown["season_name"] == "S3" and shown["season_stats"]["season_id"] == "s3" and shown["career"] == CAREER
    assert shown["team_name"] == "Falcons"
    sent = interaction.followup.send.await_args
    assert set(sent.kwargs) == {"file"}
