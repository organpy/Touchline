import pytest
import discord

from utils import branding
from utils.embed_builder import EmbedBuilder
from utils.team_colors import clear_cache, resolve_color


def test_footer_injection():
    clear_cache()
    branding.set_league_name(None)
    builder = EmbedBuilder.generic(title="Test", description="Desc")
    embed = builder.build()
    assert embed.footer.text.startswith("Touchline")


def test_footer_uses_configured_league_name():
    branding.apply_config({"league_name": "  National Bobble League  "})
    try:
        embed = EmbedBuilder.generic(title="Test", description="Desc").build()
        assert embed.footer.text.startswith("National Bobble League")
    finally:
        branding.set_league_name(None)


def test_blank_league_name_falls_back_to_default():
    branding.apply_config({"league_name": "   "})
    assert branding.get_league_name() == "Touchline"
    branding.apply_config(None)
    assert branding.get_league_name() == "Touchline"


def test_force_color_overrides_team_division(monkeypatch):
    clear_cache()
    # monkeypatch resolve_color to verify call path
    calls = {}
    def fake_resolve(team_code=None, division_code=None, force_color=None):
        calls['params'] = (team_code, division_code, force_color)
        return int("FF0000", 16)
    monkeypatch.setattr("utils.embed_builder.resolve_color", fake_resolve)
    builder = EmbedBuilder.generic(title="X", description="Y", team_code="titans", division_code="NBL", force_color="#00FF00")
    embed = builder.build()
    # Color int we returned
    assert embed.color.value == int("FF0000", 16)
    assert calls['params'] == ("titans", "NBL", "#00FF00")


def test_status_builders_colors():
    err = EmbedBuilder.error(description="Failure").build()
    ok = EmbedBuilder.success(description="Done").build()
    warn = EmbedBuilder.warning(description="Heads up").build()
    assert err.color.value == int("ED4245", 16)
    assert ok.color.value == int("57F287", 16)
    assert warn.color.value == int("FAA61A", 16)


def test_transaction_builder_uses_success_color():
    b = EmbedBuilder.transaction(action="Signed", player_name="Player", team_code="titans").build()
    assert b.color.value == int("57F287", 16)
