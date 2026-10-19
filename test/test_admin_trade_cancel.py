"""Tests for cancelling a pending trade from the admin dashboard."""

from unittest.mock import AsyncMock, MagicMock, Mock

from cogs.admin.trades import CancelTradeModal

TRADE_ID = "123e4567-e89b-12d3-a456-426614174000"


def _modal(resolved: bool, reason: str = "Collusion"):
    trade = {
        "id": TRADE_ID,
        "status": "pending",
        "metadata": {"notes": "2 for 1", "initiator_discord_id": 5},
    }
    supabase = MagicMock()
    lookup = supabase.table.return_value.select.return_value.eq.return_value.limit.return_value
    lookup.execute.return_value = MagicMock(data=[trade])
    repo = Mock(resolve_pending_trade=AsyncMock(return_value=resolved))

    modal = CancelTradeModal(supabase, repo)
    modal.trade_id._value = TRADE_ID
    modal.reason._value = reason
    return modal, repo


def _interaction():
    interaction = Mock()
    interaction.user = Mock(id=99, mention="<@99>")
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_cancel_only_applies_while_pending_and_keeps_notes():
    modal, repo = _modal(resolved=True)
    interaction = _interaction()

    await modal.on_submit(interaction)

    repo.resolve_pending_trade.assert_awaited_once_with(
        TRADE_ID,
        "cancelled",
        reviewed_by=99,
        metadata={"notes": "2 for 1", "initiator_discord_id": 5, "cancelled_by": 99, "cancel_reason": "Collusion"},
    )
    assert interaction.followup.send.await_args.kwargs["embed"].title == "Trade Cancelled"


async def test_trade_resolved_in_the_meantime_is_not_reported_cancelled():
    modal, _ = _modal(resolved=False)
    interaction = _interaction()

    await modal.on_submit(interaction)

    message = interaction.followup.send.await_args.args[0]
    assert "could not be cancelled" in message
    assert "embed" not in interaction.followup.send.await_args.kwargs
