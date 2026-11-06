"""The career query behind the player card."""

from unittest.mock import MagicMock, Mock

import pytest

from db import queries


def _supabase(data=None, error=None):
    supabase = MagicMock()
    call = supabase.rpc.return_value.execute
    if error:
        call.side_effect = error
    else:
        call.return_value = Mock(data=data)
    return supabase


@pytest.mark.asyncio
async def test_career_is_one_database_call_for_every_season():
    rows = [{"season_number": 1, "goals": 4}, {"season_number": 2, "goals": 9}]
    supabase = _supabase(rows)

    assert await queries.get_player_career(supabase, "p1") == rows
    supabase.rpc.assert_called_once_with("get_player_career", {"p_player_id": "p1"})


@pytest.mark.asyncio
async def test_a_player_without_games_has_an_empty_career():
    assert await queries.get_player_career(_supabase(None), "p1") == []
    assert await queries.get_player_career(_supabase([]), "p1") == []


@pytest.mark.asyncio
async def test_a_database_error_gives_an_empty_career():
    assert await queries.get_player_career(_supabase(error=ConnectionError("down")), "p1") == []
