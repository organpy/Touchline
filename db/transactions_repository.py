"""Repository helpers for transactions-related Supabase access."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TransactionsRepository:
    """Thin wrapper around Supabase table calls for transactions domain."""

    def __init__(self, supabase):
        self.supabase = supabase

    # ------------------------------------------------------------------
    # Draft picks
    # ------------------------------------------------------------------
    def list_draft_picks(self, season_id: str) -> List[Dict[str, Any]]:
        """Return all draft picks for a season."""
        try:
            response = (
                self.supabase
                .table('draft_picks')
                .select('*')
                .eq('season_id', season_id)
                .order('round')
                .order('overall')
                .execute()
            )
            return response.data or []
        except Exception as exc:  # pragma: no cover - network failure path
            logger.error("Error fetching draft picks for %s: %s", season_id, exc)
            return []

    def update_draft_pick_owner(self, pick_id: str, new_team_id: str) -> Optional[Dict[str, Any]]:
        """Assign the draft pick to a new team."""
        try:
            response = (
                self.supabase
                .table('draft_picks')
                .update({'current_owner_team_id': new_team_id})
                .eq('id', pick_id)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to update draft pick %s owner -> %s: %s", pick_id, new_team_id, exc)
            return None

    # ------------------------------------------------------------------
    # Pending signings
    # ------------------------------------------------------------------
    def create_pending_signing(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Persist a pending signing request. Payload should include guild, team, player, expiry, etc."""
        try:
            response = self.supabase.table('pending_signings').insert(payload).execute()
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to create pending signing %s: %s", payload, exc)
            return None

    def get_pending_signing(self, signing_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a pending signing by id."""
        try:
            response = (
                self.supabase
                .table('pending_signings')
                .select('*')
                .eq('id', signing_id)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to get pending signing %s: %s", signing_id, exc)
            return None

    def delete_pending_signing(self, signing_id: str) -> bool:
        """Remove a pending signing."""
        try:
            self.supabase.table('pending_signings').delete().eq('id', signing_id).execute()
            return True
        except Exception as exc:
            logger.error("Failed to delete pending signing %s: %s", signing_id, exc)
            return False

    def update_pending_signing(self, signing_id: str, payload: Dict[str, Any]) -> bool:
        """Update arbitrary fields on a pending signing."""
        if not payload:
            return True
        try:
            self.supabase.table('pending_signings').update(payload).eq('id', signing_id).execute()
            return True
        except Exception as exc:
            logger.error("Failed to update pending signing %s -> %s: %s", signing_id, payload, exc)
            return False

    def update_pending_signing_status(self, signing_id: str, status: str) -> bool:
        """Shortcut for updating pending signing status."""
        return self.update_pending_signing(signing_id, {'status': status})

    def cancel_other_pending_signings(self, player_id: str, keep_id: Optional[str] = None) -> None:
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
            query.execute()
        except Exception as exc:
            logger.error("Failed to cancel other pending signings for %s: %s", player_id, exc)

    def get_active_roster_entry(self, season_id: str, player_id: str) -> Optional[Dict[str, Any]]:
        """Return the active roster entry for a player in a season, if any."""
        try:
            response = (
                self.supabase
                .table('rosters')
                .select('*')
                .eq('season_id', season_id)
                .eq('player_id', player_id)
                .is_('left_at', 'null')
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to fetch roster entry for player %s season %s: %s", player_id, season_id, exc)
            return None

    def transfer_player_between_teams(
        self,
        season_id: str,
        player_id: str,
        from_team_id: str,
        to_team_id: str
    ) -> bool:
        """Mark player as left from one team and join another in same season."""
        try:
            now = datetime.utcnow().isoformat() + 'Z'

            # Mark old roster entry as left
            self.supabase.table('rosters').update(
                {'left_at': now}
            ).eq('season_id', season_id).eq('player_id', player_id).eq(
                'team_id', from_team_id
            ).is_('left_at', 'null').execute()

            # Insert new roster entry
            self.supabase.table('rosters').insert({
                'season_id': season_id,
                'team_id': to_team_id,
                'player_id': player_id,
                'joined_at': now
            }).execute()

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

    # ------------------------------------------------------------------
    # Transactions log
    # ------------------------------------------------------------------
    def insert_transaction(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Insert a transaction row and return the created record."""
        try:
            response = self.supabase.table('transactions').insert(payload).execute()
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to insert transaction %s: %s", payload, exc)
            return None

    def list_transactions_for_team(self, team_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        """Return recent transactions for a team."""
        try:
            response = (
                self.supabase
                .table('transactions')
                .select('*')
                .or_(f"from_team_id.eq.{team_id},to_team_id.eq.{team_id}")
                .order('created_at', desc=True)
                .limit(limit)
                .execute()
            )
            return response.data or []
        except Exception as exc:
            logger.error("Failed to list transactions for team %s: %s", team_id, exc)
            return []

    # ------------------------------------------------------------------
    # Trade proposals
    # ------------------------------------------------------------------
    def create_trade_proposal(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a trade proposal shell."""
        try:
            response = self.supabase.table('trade_proposals').insert(payload).execute()
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to create trade proposal %s: %s", payload, exc)
            return None

    def attach_trade_assets(self, assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Insert trade assets rows in bulk."""
        if not assets:
            return []
        try:
            response = self.supabase.table('trade_assets').insert(assets).execute()
            return response.data or []
        except Exception as exc:
            logger.error("Failed to insert trade assets %s: %s", assets, exc)
            return []

    def update_trade_status(self, proposal_id: str, status: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update trade status (pending, accepted, declined, executed)."""
        update_payload: Dict[str, Any] = {'status': status}
        if metadata:
            update_payload['metadata'] = metadata
        try:
            self.supabase.table('trade_proposals').update(update_payload).eq('id', proposal_id).execute()
            return True
        except Exception as exc:
            logger.error("Failed to update trade %s -> %s: %s", proposal_id, status, exc)
            return False

    def get_trade_with_assets(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a trade proposal along with its assets."""
        try:
            response = (
                self.supabase
                .table('trade_proposals')
                .select('*, trade_assets(*)')
                .eq('id', proposal_id)
                .limit(1)
                .execute()
            )
            return response.data[0] if response.data else None
        except Exception as exc:
            logger.error("Failed to fetch trade %s with assets: %s", proposal_id, exc)
            return None
