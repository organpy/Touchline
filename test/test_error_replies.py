"""Replies to users must not leak exception text (table names, SQL, stack details)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.cards.player_card import PlayerCardCog

SECRET = 'relation "secret_internal_table" does not exist'


def _interaction():
    interaction = Mock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_player_card_error_reply_hides_exception_text():
    interaction = _interaction()
    cog = PlayerCardCog.__new__(PlayerCardCog)

    with patch("cogs.cards.player_card.TeamPlayerCache", side_effect=RuntimeError(SECRET)):
        await PlayerCardCog.player_card_command.callback(cog, interaction, "Ace")

    reply = interaction.followup.send.await_args
    assert "secret_internal_table" not in reply.args[0]
    assert "Something went wrong" in reply.args[0]
    assert reply.kwargs["ephemeral"] is True
