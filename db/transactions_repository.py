"""Repository helpers for transactions-related Supabase access."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from postgrest.exceptions import APIError
from .db_utils import execute_async

logger = logging.getLogger(__name__)

# Error codes raised (as the HINT) by the execute_trade_proposal database function.
TRADE_ERROR_MESSAGES: Dict[str, str] = {
    'TRADE_NOT_FOUND': "This trade proposal no longer exists.",
    'TRADE_NOT_PENDING': "This trade has already been resolved.",
    'TRADE_EXPIRED': "This trade proposal has expired.",
    'TRADE_PLAYER_UNAVAILABLE': "A player in this trade is no longer on the expected team.",
    'TRADE_PICK_UNAVAILABLE': "A draft pick in this trade is no longer owned by the expected team.",
    'TRADE_HAS_NO_ASSETS': "This trade has no players or picks to move.",
    'TRADE_INVALID_ASSET': "This trade contains an invalid asset.",
    'TRADE_FAILED': "The trade could not be executed. Please contact an admin.",
}


class TradeExecutionError(Exception):
    """Raised when the database refuses to execute a trade proposal."""

    def __init__(self, code: str, detail: Optional[str] = None):
        super().__init__(detail or code)
        self.code = code if code in TRADE_ERROR_MESSAGES else 'TRADE_FAILED'

    @property
    def user_message(self) -> str:
        return TRADE_ERROR_MESSAGES[self.code]


class TransactionsRepository:
    """Thin wrapper around Supabase table calls for transactions domain."""

    def __init__(self, supabase):
        self.supabase = supabase

    # ------------------------------------------------------------------
    # Draft picks
    # ------------------------------------------------------------------
    async def list_draft_picks(self, season_id: str) -> List[Dict[str, Any]]:
        """Return all draft picks for a season."""
        try:
            response = await execute_async(
                self.supabase
                .table('draft_picks')
                .select('*')
                .eq('season_id', season_id)
                .order('round')
                .order('overall')
            )
            return response.data or []
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Error fetching draft picks for %s: %s", season_id, exc)
            return []

    async def update_draft_pick_owner(self, pick_id: str, new_team_id: str) -> Optional[Dict[str, Any]]:
        """Assign the draft pick to a new team."""
        try:
            response = await execute_async(
                self.supabase
                .table('draft_picks')
                .update({'current_owner_team_id': new_team_id})
                .eq('id', pick_id)
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to update draft pick %s owner -> %s: %s", pick_id, new_team_id, exc)
            return None

    # ------------------------------------------------------------------
    # Pending signings
    # ------------------------------------------------------------------
    async def create_pending_signing(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Persist a pending signing request. Payload should include guild, team, player, expiry, etc."""
        try:
            response = await execute_async(self.supabase.table('pending_signings').insert(payload))
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to create pending signing %s: %s", payload, exc)
            return None

    async def get_pending_signing(self, signing_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a pending signing by id."""
        try:
            response = await execute_async(
                self.supabase
                .table('pending_signings')
                .select('*')
                .eq('id', signing_id)
                .limit(1)
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to get pending signing %s: %s", signing_id, exc)
            return None

    async def delete_pending_signing(self, signing_id: str) -> bool:
        """Remove a pending signing."""
        try:
            await execute_async(self.supabase.table('pending_signings').delete().eq('id', signing_id))
            return True
        except Exception as exc:
            logger.error("Failed to delete pending signing %s: %s", signing_id, exc)
            return False

    async def update_pending_signing(self, signing_id: str, payload: Dict[str, Any]) -> bool:
        """Update arbitrary fields on a pending signing."""
        if not payload:
            return True
        try:
            await execute_async(self.supabase.table('pending_signings').update(payload).eq('id', signing_id))
            return True
        except Exception as exc:
            logger.error("Failed to update pending signing %s -> %s: %s", signing_id, payload, exc)
            return False

    async def update_pending_signing_status(self, signing_id: str, status: str) -> bool:
        """Shortcut for updating pending signing status."""
        return await self.update_pending_signing(signing_id, {'status': status})

    async def claim_pending_signing(self, signing_id: str, status: str) -> bool:
        """Move a signing from pending to status in one update. Returns False if it was no longer pending."""
        try:
            response = await execute_async(
                self.supabase
                .table('pending_signings')
                .update({'status': status})
                .eq('id', signing_id)
                .eq('status', 'pending')
            )
            return bool(response.data)
        except Exception as exc:
            logger.error("Failed to claim signing %s -> %s: %s", signing_id, status, exc)
            return False

    async def cancel_other_pending_signings(self, player_id: str, keep_id: Optional[str] = None) -> None:
        """Set all pending signings for player to declined, except keep_id."""
        try:
            query = (
                self.supabase
                .table('pending_signings')
                .update({'status': 'declined'})
                .eq('player_id', player_id)
                .eq('status', 'pending')
            )
            if keep_id:
                query = query.neq('id', keep_id)
            await execute_async(query)
        except Exception as exc:
            logger.error("Failed to cancel other pending signings for %s: %s", player_id, exc)

    async def get_active_roster_entry(self, season_id: str, player_id: str) -> Optional[Dict[str, Any]]:
        """Return the active roster entry for a player in a season, if any."""
        try:
            response = await execute_async(
                self.supabase
                .table('rosters')
                .select('*')
                .eq('season_id', season_id)
                .eq('player_id', player_id)
                .is_('left_at', 'null')
                .limit(1)
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to fetch roster entry for player %s season %s: %s", player_id, season_id, exc)
            return None

    async def transfer_player_between_teams(
        self,
        season_id: str,
        player_id: str,
        from_team_id: str,
        to_team_id: str
    ) -> bool:
        """Mark player as left from one team and join another in same season."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Mark old roster entry as left
            await execute_async(self.supabase.table('rosters').update(
                {'left_at': now}
            ).eq('season_id', season_id).eq('player_id', player_id).eq(
                'team_id', from_team_id
            ).is_('left_at', 'null'))

            # Insert new roster entry
            await execute_async(self.supabase.table('rosters').insert({
                'season_id': season_id,
                'team_id': to_team_id,
                'player_id': player_id,
                'joined_at': now
            }))

            return True
        except Exception as exc:
            logger.error(
                "Failed to transfer player %s from %s to %s: %s",
                player_id,
                from_team_id,
                to_team_id,
                exc
            )
            return False

    async def mark_player_released(self, season_id: str, player_id: str, from_team_id: str) -> bool:
        """Mark player as released (left from team without joining another)."""
        try:
            now = datetime.now(timezone.utc).isoformat()

            # Mark roster entry as left
            await execute_async(self.supabase.table('rosters').update(
                {'left_at': now}
            ).eq('season_id', season_id).eq('player_id', player_id).eq(
                'team_id', from_team_id
            ).is_('left_at', None))

            return True
        except Exception as exc:
            logger.error(
                "Failed to mark player %s as released from %s: %s",
                player_id,
                from_team_id,
                exc
            )
            return False

    # ------------------------------------------------------------------
    # Transactions log
    # ------------------------------------------------------------------
    async def insert_transaction(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Insert a transaction row and return the created record. Also creates a transaction_event.

        Accepts either the workflow shape (transaction_type / team_id / metadata) or the
        table's own column names (type / from_team_id / to_team_id / executed_by / details).
        """
        try:
            metadata = payload.get('metadata') or payload.get('details') or {}
            transaction_type = payload.get('transaction_type') or payload.get('type') or 'unknown'

            # Map transaction payload to transactions table schema
            tx_payload = {
                'season_id': payload.get('season_id'),
                'type': self._map_transaction_type(transaction_type),
                'player_id': payload.get('player_id'),
                'from_team_id': payload.get('from_team_id') or metadata.get('from_team_id'),
                'to_team_id': payload.get('to_team_id') or payload.get('team_id'),  # destination team
                'executed_by': payload.get('executed_by') or metadata.get('admin_user_id'),
                'details': metadata,
            }
            
            # Remove None values
            tx_payload = {k: v for k, v in tx_payload.items() if v is not None}
            
            # Insert transaction record
            tx_response = await execute_async(self.supabase.table('transactions').insert(tx_payload))
            transaction = tx_response.data[0] if tx_response.data else None
            
            if transaction:
                # Also create a transaction_event for the audit trail
                event_payload = {
                    'transaction_id': transaction.get('id'),
                    'event_type': transaction_type,
                    'payload': payload,
                }
                try:
                    await execute_async(self.supabase.table('transaction_events').insert(event_payload))
                except Exception as e:
                    logger.warning(f"Failed to create transaction_event: {e}")
            
            return transaction
        except Exception as exc:
            logger.error("Failed to insert transaction %s: %s", payload, exc)
            return None

    def _map_transaction_type(self, transaction_type: str) -> str:
        """Map transaction_type names to database transaction.type values."""
        mapping = {
            'admin_force_sign': 'sign',
            'admin_force_release': 'release',
            'admin_force_transfer': 'transfer',
            'draft_pick': 'sign',
            'trade': 'transfer',
        }
        return mapping.get(transaction_type, transaction_type)

    async def list_transactions_for_team(self, team_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Return recent transactions for a team."""
        try:
            response = await execute_async(
                self.supabase
                .table('transactions')
                .select('*')
                .or_(f"from_team_id.eq.{team_id},to_team_id.eq.{team_id}")
                .order('created_at', desc=True)
                .limit(limit)
            )
            return response.data or []
        except Exception as exc:
            logger.error("Failed to list transactions for team %s: %s", team_id, exc)
            return []

    # ------------------------------------------------------------------
    # Trade proposals
    # ------------------------------------------------------------------
    async def create_trade_proposal(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a trade proposal shell."""
        try:
            response = await execute_async(self.supabase.table('trade_proposals').insert(payload))
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to create trade proposal %s: %s", payload, exc)
            return None

    async def attach_trade_assets(self, assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Insert trade assets rows in bulk."""
        if not assets:
            return []
        try:
            response = await execute_async(self.supabase.table('trade_assets').insert(assets))
            return response.data or []
        except Exception as exc:
            logger.error("Failed to insert trade assets %s: %s", assets, exc)
            return []

    async def update_trade_status(self, proposal_id: str, status: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update trade status (pending, accepted, declined, executed)."""
        update_payload: Dict[str, Any] = {'status': status}
        if metadata:
            update_payload['metadata'] = metadata
        try:
            await execute_async(self.supabase.table('trade_proposals').update(update_payload).eq('id', proposal_id))
            return True
        except Exception as exc:
            logger.error("Failed to update trade %s -> %s: %s", proposal_id, status, exc)
            return False

    async def resolve_pending_trade(
        self,
        proposal_id: str,
        status: str,
        reviewed_by: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Move a still-pending proposal to a final status. Returns False if it was no longer pending.

        metadata replaces the proposal's whole metadata column, so pass the merged dict.
        """
        update_payload: Dict[str, Any] = {
            'status': status,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        if reviewed_by is not None:
            update_payload['reviewed_by'] = reviewed_by
        if metadata is not None:
            update_payload['metadata'] = metadata
        try:
            response = await execute_async(
                self.supabase
                .table('trade_proposals')
                .update(update_payload)
                .eq('id', proposal_id)
                .eq('status', 'pending')
            )
            return bool(response.data)
        except Exception as exc:
            logger.error("Failed to resolve trade %s -> %s: %s", proposal_id, status, exc)
            return False

    async def execute_trade_proposal(self, proposal_id: str, executed_by: int) -> int:
        """
        Atomically execute a pending trade proposal (rosters, picks, transaction log, status).

        Returns the number of assets moved. Raises TradeExecutionError if the database refuses
        the trade; in that case nothing was changed.
        """
        try:
            response = await execute_async(self.supabase.rpc(
                'execute_trade_proposal',
                {'p_proposal_id': proposal_id, 'p_executed_by': executed_by},
            ))
        except APIError as exc:
            logger.warning("Trade %s refused by database: %s (%s)", proposal_id, exc.message, exc.hint)
            raise TradeExecutionError(exc.hint or 'TRADE_FAILED', exc.message) from exc
        except Exception as exc:
            logger.error("Failed to execute trade %s: %s", proposal_id, exc, exc_info=True)
            raise TradeExecutionError('TRADE_FAILED', str(exc)) from exc
        return int(response.data or 0)

    async def get_trade_with_assets(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a trade proposal along with its assets."""
        try:
            response = await execute_async(
                self.supabase
                .table('trade_proposals')
                .select('*, trade_assets(*)')
                .eq('id', proposal_id)
                .limit(1)
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to fetch trade %s with assets: %s", proposal_id, exc)
            return None
