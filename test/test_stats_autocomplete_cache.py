"""Tests for the stats autocomplete memoization."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cogs.stats.autocomplete import _memoize_autocomplete


def _interaction(user_id=1, team=None):
    return Mock(user=Mock(id=user_id), namespace=SimpleNamespace(team=team))


@pytest.fixture
def echo():
    calls = []

    @_memoize_autocomplete(ttl_seconds=120)
    async def autocomplete(interaction, current):
        calls.append(current)
        return [f"{current}:{interaction.namespace.team}"]

    autocomplete.calls = calls
    return autocomplete


@pytest.mark.asyncio
async def test_each_keystroke_gets_its_own_results(echo):
    # discord.py calls autocomplete callbacks positionally: (interaction, current)
    first = await echo(_interaction(), "jo")
    second = await echo(_interaction(), "joh")

    assert first == ["jo:None"]
    assert second == ["joh:None"]


@pytest.mark.asyncio
async def test_repeated_input_is_served_from_cache(echo):
    await echo(_interaction(), "jo")
    await echo(_interaction(), "jo")

    assert echo.calls == ["jo"]


@pytest.mark.asyncio
async def test_team_filter_is_part_of_the_key(echo):
    assert await echo(_interaction(team="ghosts"), "jo") == ["jo:ghosts"]
    assert await echo(_interaction(team="titans"), "jo") == ["jo:titans"]
