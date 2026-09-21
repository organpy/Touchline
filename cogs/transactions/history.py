"""Transaction history query and display with server-side pagination support."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands

from db import queries
from utils.embed_builder import EmbedBuilder
from utils.team_display import format_team_label_from_record
from db.db_utils import execute_async

if TYPE_CHECKING:  # pragma: no cover - hints only
    from .transactions import TransactionsCog

logger = logging.getLogger(__name__)


class TransactionHistoryPaginator:
    """Paginate transaction history with filters using server-side pagination."""

    def __init__(
        self,
        transactions: List[Dict[str, Any]],
        page_size: int = 10
    ):
        """Initialize paginator with pre-fetched transactions (client-side) or offset for server-side."""
        self.transactions = transactions
        self.page_size = page_size
        self.current_page = 0
        self.total_pages = (len(transactions) + page_size - 1) // page_size

    @classmethod
    async def from_supabase(
        cls,
        supabase,
        offset: int = 0,
        limit: int = 10
    ) -> tuple[List[Dict[str, Any]], int]:
        """Fetch paginated transactions directly from Supabase (server-side pagination)."""
        try:
            logger.debug("Fetching transactions with offset=%d, limit=%d", offset, limit)
            response = await execute_async(
                supabase
                .table("transaction_events")
                .select("*")
                .order("created_at", desc=True)
                .offset(offset)
                .limit(limit)
            )
            transactions = response.data or []
            return transactions, offset + limit
        except Exception as exc:
            logger.error("Failed to fetch paginated transactions: %s", exc)
            return [], offset

    def get_page(self, page_num: int = 0) -> tuple[List[Dict[str, Any]], int, int]:
        """Return transactions for a page and page info."""
        self.current_page = max(0, min(page_num, self.total_pages - 1))
        start = self.current_page * self.page_size
        end = start + self.page_size
        return self.transactions[start:end], self.current_page, self.total_pages

    async def build_embed(
        self,
        team_by_id: Dict[str, Dict[str, Any]],
        player_by_id: Dict[str, Dict[str, Any]],
        page_num: int = 0
    ) -> discord.Embed:
        """Build an embed for a specific page."""
        page_txns, current, total = self.get_page(page_num)

        builder = EmbedBuilder(
            title="Transaction History",
            description=f"Page {current + 1} of {total}"
        )

        if not page_txns:
            builder.embed.description = "No transactions found."
            return builder.embed

        for txn in page_txns:
            timestamp = txn.get('created_at', '')
            txn_type = txn.get('transaction_type', 'unknown').upper()
            txn_id = txn.get('id', 'unknown')
            player_id = txn.get('player_id')
            from_team_id = txn.get('from_team_id')
            to_team_id = txn.get('to_team_id')

            from_team_record = team_by_id.get(from_team_id) if from_team_id else None
            to_team_record = team_by_id.get(to_team_id) if to_team_id else None

            from_team = format_team_label_from_record(from_team_record, fallback="—") if from_team_id else '—'
            to_team = format_team_label_from_record(to_team_record, fallback="—") if to_team_id else '—'

            player_name = '?'
            if player_id:
                player = player_by_id.get(player_id, {})
                player_name = player.get('display_name', '?')

            title = f"{txn_type}: {player_name}"
            date_display = timestamp[:10] if isinstance(timestamp, str) else ''
            desc = (
                f"{from_team} → {to_team}\n"
                f"ID: `{txn_id}`"
                + (f" • {date_display}" if date_display else '')
            )

            builder.add_field(name=title, value=desc, inline=False)

        builder.set_footer_text(f"Total: {len(self.transactions)} transactions")
        return builder.embed


def build_history_embed(
    transactions: List[Dict[str, Any]],
    team_by_id: Dict[str, Dict[str, Any]],
    player_by_id: Dict[str, Dict[str, Any]]
) -> discord.Embed:
    """Quick single-embed history summary (for small result sets)."""
    builder = EmbedBuilder(
        title="Recent Transactions",
        description=""
    )

    if not transactions:
        builder.embed.description = "No transactions found."
        return builder.embed

    for txn in transactions[:5]:  # Show last 5
        txn_type = txn.get('type', 'unknown').upper()
        player_id = txn.get('player_id')
        from_team_id = txn.get('from_team_id')
        to_team_id = txn.get('to_team_id')

        from_team_record = team_by_id.get(from_team_id) if from_team_id else None
        to_team_record = team_by_id.get(to_team_id) if to_team_id else None

        from_team = format_team_label_from_record(from_team_record, fallback="—") if from_team_id else '—'
        to_team = format_team_label_from_record(to_team_record, fallback="—") if to_team_id else '—'

        player_name = '?'
        if player_id:
            player = player_by_id.get(player_id, {})
            player_name = player.get('display_name', '?')

        title = f"{txn_type}: {player_name}"
        value = f"{from_team} → {to_team}"

        builder.add_field(title, value, inline=False)

    builder.set_footer_text(f"Total: {len(transactions)} transaction(s)")
    return builder.embed


async def fetch_filtered_transactions(
    repo,
    supabase,
    season_id: str,
    team_code: Optional[str] = None,
    player_name: Optional[str] = None,
    txn_type: Optional[str] = None,
    days: int = 30
) -> List[Dict[str, Any]]:
    """
    Fetch transactions with optional filters.
    
    Args:
        repo: TransactionsRepository instance
        supabase: Supabase client
        season_id: Season to filter by
        team_code: Optional team code to filter by
        player_name: Optional player name to filter by
        txn_type: Optional transaction type ('sign', 'release', 'trade')
        days: Look back this many days
    
    Returns:
        List of transaction dicts matching filters
    """
    try:
        # Build base query
        since = (datetime.utcnow() - timedelta(days=days)).isoformat() + 'Z'

        query = supabase.table('transactions').select(
            '*, players(id, display_name), '
            'from_team:from_team_id(id, code, name), '
            'to_team:to_team_id(id, code, name)'
        ).eq('season_id', season_id).gte('created_at', since).order(
            'created_at', desc=True
        )

        if txn_type:
            query = query.eq('type', txn_type)

        response = await execute_async(query)
        transactions = response.data or []

        # Client-side filtering for team and player
        if team_code or player_name:
            team_code_lower = team_code.lower() if team_code else None
            player_name_lower = player_name.lower() if player_name else None

            filtered = []
            for txn in transactions:
                # Team filter
                if team_code_lower:
                    from_team = txn.get('from_team', {}) or {}
                    to_team = txn.get('to_team', {}) or {}
                    from_code = from_team.get('code', '').lower() if from_team else ''
                    to_code = to_team.get('code', '').lower() if to_team else ''

                    if team_code_lower not in (from_code, to_code):
                        continue

                # Player filter
                if player_name_lower:
                    player = txn.get('players', {})
                    player_display = player.get('display_name', '').lower() if player else ''
                    if player_name_lower not in player_display:
                        continue

                filtered.append(txn)

            return filtered

        return transactions

    except Exception as exc:
        logger.error("Failed to fetch filtered transactions: %s", exc)
        return []
