"""Tests for league name validation and player name sanitising."""

import pytest

from utils.validation import clean_name, sanitize_player_name


class TestCleanName:
    def test_trims_and_collapses_whitespace(self):
        assert clean_name("  Frost   Falcons ") == "Frost Falcons"

    @pytest.mark.parametrize("name", [
        "@everyone", "Go @Everyone Go", "@here", "x<@123>x", "<@&456>", "<#789>",
        "Team`s", "two\nlines", "tab\tname", "nul\x00",
    ])
    def test_rejects_unsafe_names(self, name):
        with pytest.raises(ValueError):
            clean_name(name, "Team name")

    @pytest.mark.parametrize("name", ["Falcons #1", "O'Brien & Sons", "Équipe Étoile", "Team @ Home", "D1 — Élite"])
    def test_accepts_normal_names(self, name):
        assert clean_name(name) == name

    def test_rejects_empty_and_too_long(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            clean_name("   ", "Team name")
        with pytest.raises(ValueError, match="at most 10"):
            clean_name("x" * 11, "Team name", max_len=10)

    def test_message_names_the_field(self):
        with pytest.raises(ValueError, match="^Division name"):
            clean_name("@everyone", "Division name")


class TestSanitizePlayerName:
    def test_strips_unsafe_characters(self):
        cleaned = sanitize_player_name("Ace `@everyone` <@1>")
        assert cleaned == "Ace @1"
        assert not any(ch in cleaned for ch in "`<>")

    def test_keeps_normal_names(self):
        assert sanitize_player_name("Zoë  O'Neil") == "Zoë O'Neil"

    def test_falls_back_when_nothing_is_left(self):
        assert sanitize_player_name("@everyone") == "Unknown player"
        assert sanitize_player_name("") == "Unknown player"

    def test_limits_length(self):
        assert len(sanitize_player_name("x" * 200)) == 64


@pytest.mark.asyncio
async def test_new_players_are_stored_with_a_safe_name():
    from unittest.mock import MagicMock

    from db import queries

    supabase = MagicMock()
    supabase.table.return_value.select.return_value.ilike.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "p1"}])

    await queries.get_or_create_player(supabase, "Ace `@everyone` <@1>", 5)

    stored = supabase.table.return_value.insert.call_args.args[0]
    assert stored["display_name"] == "Ace @1"
    assert stored["discord_id"] == 5
