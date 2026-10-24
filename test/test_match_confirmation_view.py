"""The confirm view must save a report once, however fast the buttons are clicked."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from cogs.score.views import MatchConfirmationView


def _interaction():
    interaction = Mock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_double_click_saves_once():
    gate = asyncio.Event()
    calls = []

    async def on_confirm(interaction):
        calls.append(interaction)
        await gate.wait()
        return True

    view = MatchConfirmationView(Mock(), on_confirm, AsyncMock(return_value=True))
    first = asyncio.create_task(view.confirm_button.callback(_interaction()))
    await asyncio.sleep(0)
    await view.confirm_button.callback(_interaction())  # second click while the first is still saving
    gate.set()
    await first

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_buttons_go_inactive_after_the_game_is_saved():
    interaction = _interaction()
    view = MatchConfirmationView(Mock(), AsyncMock(return_value=True), AsyncMock(return_value=True))

    await view.confirm_button.callback(interaction)

    assert all(item.disabled for item in view.children)
    interaction.edit_original_response.assert_awaited_once_with(view=view)
    assert view.is_finished()


@pytest.mark.asyncio
async def test_a_failed_save_can_be_retried():
    on_confirm = AsyncMock(side_effect=[False, True])
    view = MatchConfirmationView(Mock(), on_confirm, AsyncMock(return_value=True))

    await view.confirm_button.callback(_interaction())
    assert not any(item.disabled for item in view.children)

    await view.confirm_button.callback(_interaction())
    assert on_confirm.await_count == 2
    assert all(item.disabled for item in view.children)

