from unittest.mock import MagicMock, Mock

import pytest
from postgrest.exceptions import APIError

from db.league_repository import (
    LeagueRepository,
    LeagueRuleError,
    friendly_error,
    normalize_division_code,
    normalize_team_code,
    rule_error_from_api_error,
    run,
)


def api_error(**fields):
    base = {"message": "boom", "code": "P0001", "hint": None, "details": None}
    base.update(fields)
    return APIError(base)


def failing_query(exc):
    query = Mock()
    query.execute.side_effect = exc
    return query


def ok_query(data):
    query = Mock()
    query.execute.return_value = Mock(data=data)
    return query


def test_known_hint_becomes_rule_error():
    error = rule_error_from_api_error(api_error(hint="MATCH_NOT_REGULAR_PHASE"))
    assert error.code == "MATCH_NOT_REGULAR_PHASE"
    assert "regular season" in error.message


def test_unique_violation_gets_friendly_message():
    error = rule_error_from_api_error(
        api_error(code="23505", message='duplicate key value violates unique constraint "ux_teams_code"')
    )
    assert error.code == "DUPLICATE"
    assert "team with that code" in error.message


def test_unknown_error_is_not_translated():
    assert rule_error_from_api_error(api_error(code="42P01", hint="something else")) is None


def test_friendly_error_explains_one_team_rules():
    owner = api_error(code="23505", message='duplicate key value violates unique constraint "ux_teams_owner"')
    assert friendly_error(owner, "fallback") == "That user already owns a team."
    roster = api_error(hint="ROSTER_PLAYER_ON_TEAM")
    assert "already on another team" in friendly_error(roster, "fallback")
    assert friendly_error(RuntimeError("network"), "fallback") == "fallback"


@pytest.mark.asyncio
async def test_run_raises_rule_error():
    with pytest.raises(LeagueRuleError) as info:
        await run(failing_query(api_error(hint="SERIES_DOWNSTREAM_PLAYED")))
    assert info.value.code == "SERIES_DOWNSTREAM_PLAYED"


@pytest.mark.asyncio
async def test_run_passes_other_errors_through():
    with pytest.raises(APIError):
        await run(failing_query(api_error(code="42P01")))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("The Titans", "the-titans"),
        ("  ICE_breakers ", "ice-breakers"),
        ("OPO!!", "opo"),
        ("--a  b--", "a-b"),
    ],
)
def test_normalize_team_code(raw, expected):
    assert normalize_team_code(raw) == expected


def test_normalize_division_code():
    assert normalize_division_code(" d1 ") == "D1"
    assert normalize_division_code("Prem League") == "PREMLEAGUE"


@pytest.mark.asyncio
async def test_create_team_normalizes_code():
    supabase = MagicMock()
    insert = ok_query([{"id": "t1", "code": "the-pack"}])
    supabase.table.return_value.insert.return_value = insert
    repo = LeagueRepository(supabase)

    team = await repo.create_team(code="", name="The Pack", emoji="🐺")

    assert team["id"] == "t1"
    payload = supabase.table.return_value.insert.call_args.args[0]
    assert payload["code"] == "the-pack"
    assert payload["name"] == "The Pack"


@pytest.mark.asyncio
async def test_create_team_rejects_empty_code():
    repo = LeagueRepository(MagicMock())
    with pytest.raises(LeagueRuleError):
        await repo.create_team(code="!!!", name="???")


@pytest.mark.asyncio
async def test_create_division_appends_tier():
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.order.return_value.is_.return_value = ok_query(
        [{"id": "a", "tier": 1}, {"id": "b", "tier": 2}]
    )
    supabase.table.return_value.insert.return_value = ok_query([{"id": "c", "tier": 3}])
    repo = LeagueRepository(supabase)

    await repo.create_division(code="d3", name="League Two")

    payload = supabase.table.return_value.insert.call_args.args[0]
    assert payload["tier"] == 3
    assert payload["code"] == "D3"


@pytest.mark.asyncio
async def test_archive_division_refuses_when_teams_remain():
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.eq.return_value.eq.return_value = ok_query(
        [{"team_id": "t1", "teams": {"name": "A"}}]
    )
    repo = LeagueRepository(supabase)
    with pytest.raises(LeagueRuleError) as info:
        await repo.archive_division("d1", season_id="s1")
    assert info.value.code == "DIVISION_HAS_TEAMS"


@pytest.mark.asyncio
async def test_create_first_season_only_once():
    supabase = MagicMock()
    supabase.table.return_value.select.return_value.order.return_value = ok_query([{"id": "s1"}])
    repo = LeagueRepository(supabase)
    with pytest.raises(LeagueRuleError):
        await repo.create_first_season("Season 1")
