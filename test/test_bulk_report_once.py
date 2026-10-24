"""/report-stats-bulk and /report-score save one report once, however often the button is pressed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.score import bulk_stats
from cogs.score.bulk_stats import BulkStatsCog


@pytest.fixture
def cog():
    with patch.object(bulk_stats, "get_supabase", return_value=Mock()), patch.object(bulk_stats, "RosterCache"):
        return BulkStatsCog(Mock())


def _team(team_id):
    return SimpleNamespace(team_id=team_id, code=team_id.upper(), name=f"Team {team_id}", emoji=None)


def _bulk_entry(report_id="r-1"):
    return {
        "team1": _team("t1"), "team2": _team("t2"), "team1_stats": [], "team2_stats": [],
        "team1_score": 5, "team2_score": 3, "report_id": report_id,
    }


def _interaction():
    interaction = Mock()
    interaction.followup.send = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_bulk_report_is_saved_once_when_the_button_is_pressed_twice(cog):
    cog.active_entries[7] = _bulk_entry()
    gate = asyncio.Event()
    saves = []

    async def slow_save(interaction, user_id):
        saves.append(user_id)
        await gate.wait()

    with patch.object(cog, "_save_match", slow_save):
        first = asyncio.create_task(cog.save_match(_interaction(), 7))
        await asyncio.sleep(0)
        second_interaction = _interaction()
        await cog.save_match(second_interaction, 7)
        gate.set()
        await first

    assert saves == [7]
    assert "already saved" in second_interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_a_saved_bulk_report_cannot_be_saved_again(cog):
    interaction = _interaction()

    await cog.save_match(interaction, 7)  # nothing pending: it was saved (or expired)

    assert "already saved" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_a_failed_save_can_be_retried(cog):
    cog.active_entries[7] = _bulk_entry()
    with patch.object(cog, "_save_match", AsyncMock(side_effect=[None, None])) as inner:
        await cog.save_match(_interaction(), 7)
        await cog.save_match(_interaction(), 7)  # the entry was not removed, so the retry runs
    assert inner.await_count == 2
    assert "saving" not in cog.active_entries[7]


@pytest.mark.asyncio
async def test_the_stored_report_id_is_what_gets_saved(cog):
    cog.active_entries[7] = _bulk_entry(report_id="r-42")
    saved = AsyncMock(return_value=({"id": "m1"}, SimpleNamespace(stage="regular")))

    with patch.object(bulk_stats, "record_match", saved):
        await cog.save_match(_interaction(), 7)

    match_entry = saved.await_args.args[1]
    assert match_entry.report_id == "r-42"
    assert saved.await_args.kwargs["recorded_by"] == 7
    assert 7 not in cog.active_entries


@pytest.mark.asyncio
async def test_score_only_reports_keep_their_own_state(cog):
    cog.active_entries[7] = _bulk_entry()  # an unfinished /report-stats-bulk
    cog.active_scores[7] = {"team1": _team("t1"), "team2": _team("t2"), "report_id": "r-9"}
    saved = AsyncMock(return_value=({"id": "m2"}, SimpleNamespace(stage="regular")))

    with patch.object(bulk_stats, "record_match", saved):
        await cog.save_score_only(_interaction(), 7, 9, 4)

    assert saved.await_args.args[1].report_id == "r-9"
    assert 7 not in cog.active_scores
    assert 7 in cog.active_entries
