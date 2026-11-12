"""Demo data, the in-memory bracket simulator, the bracket image and the /demo commands."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord import app_commands
from PIL import Image

from services.brackets import KIND_CHAMPIONSHIP, KIND_PROMO_REL, KIND_PROMOTION, KIND_RELEGATION
from services.movements import _roots
from utils import bracket_image_generator as gen
from utils import demo_data as demo


def finals(bracket, link="winner_to"):
    return _roots(bracket["series"], link)


# ------------------------------------------------------------------------------------ simulator
@pytest.mark.parametrize("teams", [2, 3, 5, 6, 8, 12, 16])
@pytest.mark.parametrize("best_of", [1, 3])
def test_a_championship_plays_to_one_champion(teams, best_of):
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, teams, best_of=best_of), seed=teams)
    sim.play_all()

    assert sim.finished and all(s["status"] == "completed" for s in sim.series)
    assert len(finals(sim.bracket)) == 1 and finals(sim.bracket)[0]["winner_id"]
    needed = best_of // 2 + 1
    assert all(max(s["team1_wins"], s["team2_wins"]) == needed for s in sim.series)


def test_winners_move_into_the_slot_they_feed():
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 4), seed=1)
    first = sim.ready()[0]
    sim.play_series(first["id"])

    target = sim.get(first["winner_to"])
    assert target[f"team{first['winner_slot']}_id"] == first["winner_id"]
    assert target["status"] == "waiting"  # the other semifinal is still to play


def test_relegation_brackets_send_the_loser_on():
    sim = demo.BracketSim(demo.demo_bracket(KIND_RELEGATION, 4), seed=3)
    sim.play_all()

    final = finals(sim.bracket, "loser_to")
    assert len(final) == 1 and final[0]["loser_id"]
    for series in sim.series:
        if series.get("loser_to"):
            assert sim.get(series["loser_to"])[f"team{series['loser_slot']}_id"] == series["loser_id"]


def test_promotion_has_one_tree_per_spot():
    bracket = demo.demo_bracket(KIND_PROMOTION, 8, spots=2)
    assert len(gen.layout_sections(bracket["series"], KIND_PROMOTION)) == 2


def test_promo_rel_pairs_upper_and_lower_teams():
    bracket = demo.demo_bracket(KIND_PROMO_REL, 6, spots=3)
    assert len(bracket["series"]) == 3 and all(s["status"] == "ready" for s in bracket["series"])


def test_forfeits_decide_and_can_be_cleared():
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 4))
    series = sim.ready()[0]
    sim.forfeit(series["id"], series["team2_id"])
    assert series["winner_id"] == series["team2_id"] and series["status"] == "completed"

    sim.forfeit(series["id"], None)
    assert series["winner_id"] is None and series["status"] == "ready"
    assert sim.get(series["winner_to"])[f"team{series['winner_slot']}_id"] is None


def test_seeds_swap_only_before_the_first_game():
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 4))
    first = sim.ready()[0]
    a, b = first["team1_id"], first["team2_id"]

    assert sim.swap_seeds(a, b)
    assert (first["team1_id"], first["team2_id"]) == (b, a)
    sim.play_game()
    assert not sim.swap_seeds(a, b)


def test_reset_goes_back_to_the_start():
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 8), name=lambda t: t.upper())
    sim.play_all()
    assert sim.log and sim.log[0].startswith("DEMO-")
    sim.reset()
    assert not sim.started and not sim.log and sim.bracket["status"] == "active"


def test_an_unknown_kind_is_refused_and_spots_are_capped_by_the_field():
    with pytest.raises(ValueError):
        demo.demo_bracket("knockout")
    assert demo.demo_bracket(KIND_PROMOTION, 3, spots=5)["spots"] == 1


# ------------------------------------------------------------------------------------ image
@pytest.fixture(autouse=True)
def fresh_cache():
    gen.bracket_cache.clear()
    yield
    gen.bracket_cache.clear()


def render(bracket, **kwargs):
    return gen.generate_bracket_image(bracket, demo.team_map(), "Championship", "D1 · Demo", "#F7AD44", **kwargs)


@pytest.mark.parametrize("kind, teams, spots", [
    (KIND_CHAMPIONSHIP, 2, 1), (KIND_CHAMPIONSHIP, 6, 1), (KIND_CHAMPIONSHIP, 16, 1),
    (KIND_PROMOTION, 8, 2), (KIND_RELEGATION, 4, 1), (KIND_PROMO_REL, 6, 3),
])
def test_every_kind_renders_before_and_after_play(kind, teams, spots):
    sim = demo.BracketSim(demo.demo_bracket(kind, teams, spots), seed=7)
    assert render(sim.bracket) is not None
    sim.play_all()
    image = Image.open(io.BytesIO(render(sim.bracket).getvalue()))
    assert image.format == "PNG" and image.width >= gen.MIN_CONTENT_WIDTH


def test_more_rounds_make_a_wider_image_and_more_teams_a_taller_one():
    def size(teams):
        return Image.open(io.BytesIO(render(demo.demo_bracket(KIND_CHAMPIONSHIP, teams)).getvalue())).size

    (w4, h4), (w8, h8) = size(4), size(8)
    assert w8 - w4 == gen.BOX_WIDTH + gen.COLUMN_GAP
    assert h8 > h4


def test_a_series_sits_between_the_two_it_is_fed_by():
    bracket = demo.demo_bracket(KIND_CHAMPIONSHIP, 4)
    (root, _), = gen.layout_sections(bracket["series"], KIND_CHAMPIONSHIP)
    ys = [child.y for _, child in root.children]
    assert root.y == sum(ys) / 2 and root.column == 1


def test_no_series_means_no_image_and_a_failure_gives_none():
    assert gen.generate_bracket_image({"series": []}, {}, "T") is None
    with patch.object(gen, "_render", side_effect=RuntimeError("boom")):
        assert render(demo.demo_bracket(KIND_CHAMPIONSHIP, 4)) is None


def test_the_same_bracket_is_drawn_once():
    bracket = demo.demo_bracket(KIND_CHAMPIONSHIP, 4)
    with patch.object(gen, "_render", wraps=gen._render) as spy:
        render(bracket), render(bracket)
    assert spy.call_count == 1


def test_the_advancing_team_row_is_marked_in_the_accent_colour():
    sim = demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 2), seed=1)
    sim.play_all()
    image = Image.open(io.BytesIO(render(sim.bracket).getvalue())).convert("RGB")
    accent = (0xF7, 0xAD, 0x44)
    assert any(image.getpixel((x, y)) == accent for x in range(40, 80) for y in range(100, image.height - 30))


# ------------------------------------------------------------------------------------ /demo cog
@pytest.mark.asyncio
async def test_demo_commands_are_off_unless_enabled(monkeypatch):
    from cogs.demo import demo as demo_cog

    bot = MagicMock()
    bot.add_cog = AsyncMock()
    monkeypatch.delenv(demo_cog.ENV_FLAG, raising=False)
    await demo_cog.setup(bot)
    bot.add_cog.assert_not_awaited()

    monkeypatch.setenv(demo_cog.ENV_FLAG, "true")
    await demo_cog.setup(bot)
    bot.add_cog.assert_awaited_once()


def test_every_demo_command_is_admin_only():
    from cogs.demo.demo import DemoCog

    cog = DemoCog(MagicMock())
    commands = [c for c in cog.walk_app_commands() if isinstance(c, app_commands.Command)]
    assert {c.name for c in commands} == {"bracket", "playoffs", "standings", "match-table", "player-card", "gallery", "errors"}
    for command in commands:
        assert command.description.startswith("[ADMIN]")
        assert any(getattr(check, "__qualname__", "").startswith("admin_only.") for check in command.checks)
    assert cog.__cog_app_commands_group__.default_permissions.administrator


@pytest.mark.asyncio
async def test_the_playoff_view_steps_and_redraws():
    from cogs.demo.demo import PlayoffSimView, name_of

    view = PlayoffSimView(demo.BracketSim(demo.demo_bracket(KIND_CHAMPIONSHIP, 4, best_of=3), seed=2, name=name_of))
    labels = [getattr(item, "label", None) for item in view.children]
    assert "Play game" in labels and any(getattr(i, "placeholder", "").startswith("Swap") for i in view.children)

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    await view._all(interaction)

    kwargs = interaction.response.edit_message.await_args.kwargs
    assert len(kwargs["attachments"]) == 1
    assert kwargs["embed"].description.startswith("Finished")
    assert all(getattr(item, "disabled", False) for item in view.children if getattr(item, "label", "") == "Play game")
    assert not any(getattr(i, "placeholder", "") and "Swap" in i.placeholder for i in view.children)
