"""Tests for admin autocomplete without guild_divisions rows."""

from unittest.mock import AsyncMock

import pytest

from cogs.admin import autocomplete


@pytest.mark.asyncio
async def test_team_autocomplete_uses_all_divisions(monkeypatch):
    """Team autocomplete should not depend on a populated guild_divisions table."""
    monkeypatch.setattr(autocomplete, "get_supabase", lambda: object())
    monkeypatch.setattr(
        autocomplete.queries,
        "get_all_teams",
        AsyncMock(return_value=[
            {"code": "PANDAS", "name": "Golden Pandas"},
            {"code": "SHARKS", "name": "South Point Sharks"},
        ]),
    )

    choices = await autocomplete._admin_team_autocomplete(None, "pan")

    assert [choice.value for choice in choices] == ["PANDAS"]


@pytest.mark.asyncio
async def test_season_autocomplete_uses_all_divisions(monkeypatch):
    """Season autocomplete should list seasons across every division."""
    monkeypatch.setattr(autocomplete, "get_supabase", lambda: object())
    monkeypatch.setattr(
        autocomplete.queries,
        "get_all_seasons",
        AsyncMock(return_value=[
            {"name": "S10"},
            {"name": "S11"},
        ]),
    )

    choices = await autocomplete._admin_season_autocomplete(None, "S10")

    assert [choice.value for choice in choices] == ["S10"]


@pytest.mark.asyncio
async def test_division_autocomplete_uses_all_divisions(monkeypatch):
    """Division autocomplete should list every division without guild scoping."""
    monkeypatch.setattr(autocomplete, "get_supabase", lambda: object())
    monkeypatch.setattr(
        autocomplete.queries,
        "get_all_divisions",
        AsyncMock(return_value=[
            {"name": "Premier", "code": "D1"},
            {"name": "Championship", "code": "D2"},
        ]),
    )

    choices = await autocomplete._admin_division_autocomplete(None, "d1")

    assert [choice.value for choice in choices] == ["Premier"]
