"""Tests for signing offer buttons: an offer can only be answered once."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cogs.transactions import persistent_views
from cogs.transactions.persistent_views import SigningAcceptButton, SigningDeclineButton

PLAYER_DISCORD_ID = 5005


def _interaction():
    interaction = Mock()
    interaction.user = Mock(id=PLAYER_DISCORD_ID)
    interaction.response = Mock()
    interaction.response.defer = AsyncMock()
    interaction.followup = Mock()
    interaction.followup.send = AsyncMock()
    interaction.message = Mock()
    interaction.message.edit = AsyncMock()
    return interaction


@pytest.fixture
def repo():
    repo = AsyncMock()
    repo.get_pending_signing.return_value = {
        'id': 'sign-1',
        'status': 'pending',
        'player_id': 'player-1',
        'team_id': 'team-1',
        'season_id': 'season-1',
        'expires_at': None,
    }
    repo.get_active_roster_entry.return_value = None
    repo.insert_transaction.return_value = {'id': 'tx-1'}

    supabase = MagicMock()
    supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = Mock(
        data={'discord_id': PLAYER_DISCORD_ID}
    )
    with patch.object(persistent_views, "get_supabase", return_value=supabase), patch.object(
        persistent_views, "TransactionsRepository", return_value=repo
    ), patch.object(persistent_views, "RosterCache"), patch.object(persistent_views, "TeamPlayerCache") as cache:
        cache.return_value.invalidate_team = AsyncMock()
        yield repo


class TestAccept:
    @pytest.mark.asyncio
    async def test_claims_offer_before_signing(self, repo):
        repo.claim_pending_signing.return_value = True

        await SigningAcceptButton('sign-1').callback(_interaction())

        repo.claim_pending_signing.assert_called_once_with('sign-1', 'accepted')
        repo.insert_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_click_does_not_sign_again(self, repo):
        repo.claim_pending_signing.return_value = False
        interaction = _interaction()

        await SigningAcceptButton('sign-1').callback(interaction)

        repo.insert_transaction.assert_not_called()
        assert "already been answered" in interaction.followup.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_failed_signing_reopens_offer(self, repo):
        repo.claim_pending_signing.return_value = True
        repo.insert_transaction.return_value = None

        await SigningAcceptButton('sign-1').callback(_interaction())

        repo.update_pending_signing_status.assert_called_once_with('sign-1', 'pending')


class TestDecline:
    @pytest.mark.asyncio
    async def test_decline_claims_offer(self, repo):
        repo.claim_pending_signing.return_value = True

        await SigningDeclineButton('sign-1').callback(_interaction())

        repo.claim_pending_signing.assert_called_once_with('sign-1', 'declined')

    @pytest.mark.asyncio
    async def test_decline_after_accept_is_refused(self, repo):
        repo.claim_pending_signing.return_value = False
        interaction = _interaction()

        await SigningDeclineButton('sign-1').callback(interaction)

        interaction.message.edit.assert_not_awaited()
        assert "already been answered" in interaction.followup.send.await_args.args[0]
