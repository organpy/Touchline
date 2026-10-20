"""Tests for the division-first, paged team select."""

from unittest.mock import AsyncMock, Mock

import pytest

from utils.team_picker import (
    MAX_OPTIONS,
    NAV_ALL,
    NAV_DIVISION,
    NAV_NEXT,
    NAV_PREV,
    PickerDivision,
    PickerEntry,
    TeamSelect,
    build_options,
    entry_from_team,
)

D1 = PickerDivision("d1", "D1 · Premier", 1)
D2 = PickerDivision("d2", "D2 · Championship", 2)


def entries(count, division_id=None, prefix="t"):
    return [PickerEntry(f"{prefix}{i}", f"Team {prefix}{i}", division_id=division_id) for i in range(count)]


def values(options):
    return [o.value for o in options]


def test_small_league_is_a_flat_list():
    options = build_options(entries(10, "d1") + entries(10, "d2", "u"), [D1, D2])
    assert len(options) == 20
    assert not any(v.startswith("__") for v in values(options))


def test_large_league_lists_divisions_first_in_tier_order():
    teams = entries(20, "d2", "b") + entries(20, "d1", "a")
    options = build_options(teams, [D1, D2])
    assert values(options) == [f"{NAV_DIVISION}d1", f"{NAV_DIVISION}d2"]
    assert options[0].description == "20 teams"


def test_teams_without_a_division_get_their_own_bucket():
    teams = entries(20, "d1") + entries(10, None, "x")
    options = build_options(teams, [D1, D2])
    assert values(options) == [f"{NAV_DIVISION}d1", f"{NAV_DIVISION}none"]
    unassigned = build_options(teams, [D1, D2], division_id="none")
    assert values(unassigned)[0] == NAV_ALL
    assert len(unassigned) == 11


def test_choosing_a_division_shows_its_teams_with_a_back_option():
    teams = entries(20, "d1", "a") + entries(20, "d2", "b")
    options = build_options(teams, [D1, D2], division_id="d2")
    assert values(options)[0] == NAV_ALL
    assert values(options)[1:] == [f"b{i}" for i in range(20)]


def test_big_division_is_paged():
    teams = entries(60, "d1") + entries(5, "d2", "b")
    first = build_options(teams, [D1, D2], division_id="d1", page=0)
    assert len(first) <= MAX_OPTIONS
    assert values(first)[0] == NAV_ALL
    assert values(first)[-1] == NAV_NEXT
    assert NAV_PREV not in values(first)

    last = build_options(teams, [D1, D2], division_id="d1", page=99)  # clamped to the last page
    assert values(last)[1] == NAV_PREV
    assert NAV_NEXT not in values(last)

    shown = set()
    for page in range(3):
        shown |= {v for v in values(build_options(teams, [D1, D2], division_id="d1", page=page)) if not v.startswith("__")}
    assert shown == {f"t{i}" for i in range(60)}


def test_single_division_league_pages_without_division_step():
    options = build_options(entries(40, "d1"), [D1, D2])
    assert NAV_ALL not in values(options)
    assert values(options)[-1] == NAV_NEXT
    assert len(options) <= MAX_OPTIONS


def test_entry_from_team_uses_name_code_and_division():
    entry = entry_from_team({"id": "x", "code": "pan", "name": "Pandas", "division_id": "d1", "league_name": "Premier"})
    assert entry.label == "Pandas (PAN)"
    assert entry.description == "Premier"
    assert entry.division_id == "d1"
    assert entry_from_team({"id": "x", "code": "pan", "name": "Pandas"}, value_key="code").value == "pan"


@pytest.mark.asyncio
async def test_select_navigates_then_calls_on_pick():
    on_pick = AsyncMock()
    select = TeamSelect(entries(20, "d1", "a") + entries(20, "d2", "b"), on_pick=on_pick, divisions=[D1, D2])
    assert [o.value for o in select.options] == [f"{NAV_DIVISION}d1", f"{NAV_DIVISION}d2"]

    interaction = Mock()
    interaction.response.edit_message = AsyncMock()

    select._values = [f"{NAV_DIVISION}d2"]
    await select.callback(interaction)
    interaction.response.edit_message.assert_awaited_once()
    on_pick.assert_not_awaited()
    assert select.options[1].value == "b0"
    assert "D2" in select.placeholder

    select._values = ["b3"]
    await select.callback(interaction)
    on_pick.assert_awaited_once_with(interaction)
    assert select.picked == "b3"


def test_empty_select_is_disabled():
    select = TeamSelect([], on_pick=AsyncMock(), divisions=[])
    assert select.disabled
    assert select.options[0].label == "No teams found"
