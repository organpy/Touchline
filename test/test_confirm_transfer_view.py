"""Handing a team to another member must happen once, and only for the team's current owner."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cogs.transactions.transactions import ConfirmTransferView

OWNER_ID = 100
NEW_OWNER_ID = 200


def _cog(update_data):
    cog = Mock()
    cog.supabase = MagicMock()
    query = cog.supabase.table.return_value.update.return_value.eq.return_value.eq.return_value
    query.execute.return_value = Mock(data=update_data)
    return cog


def _view(cog):
    new_owner = Mock(id=NEW_OWNER_ID, mention="<@200>")
    return ConfirmTransferView(cog, {"id": "team-1", "code": "pack"}, new_owner, "The Pack")


def _click():
    interaction = Mock()
    interaction.user = Mock(id=OWNER_ID, mention="<@100>")
    interaction.guild_id = 1
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


@pytest.fixture(autouse=True)
def quiet_dependencies():
    manager = Mock(get_transactions_config=AsyncMock(return_value={}))
    with patch("cogs.transactions.transactions.TeamPlayerCache.refresh", AsyncMock()), \
         patch("utils.config_manager.get_config_manager", return_value=manager):
        yield


@pytest.mark.asyncio
async def test_transfer_only_applies_while_the_clicker_still_owns_the_team():
    cog = _cog([{"id": "team-1"}])
    view = _view(cog)

    await view.confirm.callback(_click())

    update = cog.supabase.table.return_value.update
    update.assert_called_once_with({"owner_discord_id": NEW_OWNER_ID})
    update.return_value.eq.assert_called_once_with("id", "team-1")
    update.return_value.eq.return_value.eq.assert_called_once_with("owner_discord_id", OWNER_ID)


@pytest.mark.asyncio
async def test_buttons_go_inactive_after_the_transfer():
    view = _view(_cog([{"id": "team-1"}]))
    interaction = _click()

    await view.confirm.callback(interaction)

    assert all(item.disabled for item in view.children)
    interaction.edit_original_response.assert_awaited_once_with(view=view)
    assert "has been transferred" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_a_second_click_does_not_transfer_again():
    cog = _cog([{"id": "team-1"}])
    view = _view(cog)

    await view.confirm.callback(_click())
    second = _click()
    await view.confirm.callback(second)

    assert cog.supabase.table.return_value.update.call_count == 1
    second.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_transfer_says_why_and_can_be_retried():
    cog = _cog([])  # the clicker no longer owns the team
    view = _view(cog)
    interaction = _click()

    await view.confirm.callback(interaction)

    assert "no longer own" in interaction.followup.send.await_args.args[0]
    assert view.confirmed is False
    assert not any(item.disabled for item in view.children)
