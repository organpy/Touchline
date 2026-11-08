"""
Draft pick trading.

Draft picks exist only in divisions whose settings have ``picks_enabled``; each pick belongs to one
division and season, and picks can only change hands between teams of that division.
"""

import logging
from typing import Optional, Dict, Any, List, Set

from db.db_utils import execute_async
from db.league_repository import utc_now_iso
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)


def picks_enabled_for_division(division_id: Optional[str]) -> bool:
    """True when the division runs a draft."""
    if not division_id or not LeagueCache.division(division_id):
        return False
    return LeagueCache.settings(division_id).picks_enabled


def picks_division_for_team(team_id: Optional[str], season_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The team's division in the season if that division runs a draft, else None."""
    division = LeagueCache.team_division(team_id, season_id)
    if division and picks_enabled_for_division(division.get('id')):
        return division
    return None


async def get_available_picks_for_team(
    supabase,
    team_id: str,
    season_id: str
) -> List[Dict[str, Any]]:
    """Get all available picks currently owned by a team in a season."""
    try:
        response = await execute_async(supabase.table('draft_picks').select(
            '*, teams:current_owner_team_id(id, code, name)'
        ).eq('season_id', season_id).eq('current_owner_team_id', team_id).order('overall', desc=False))
        return response.data or []
    except Exception as e:
        logger.error(f"Failed to fetch available picks for team {team_id}: {e}")
        return []


async def transfer_pick_ownership(
    supabase,
    pick_id: str,
    new_owner_id: str
) -> bool:
    """
    Transfer a draft pick to a new owner (called during trade execution).
    Keeps original_team_id unchanged (for lottery/history purposes).
    """
    try:
        result = await execute_async(supabase.table('draft_picks').update({
            'current_owner_team_id': new_owner_id,
            'updated_at': utc_now_iso(),
        }).eq('id', pick_id))

        if result.data:
            logger.info(f"✅ Pick {pick_id} transferred to team {new_owner_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to transfer pick {pick_id}: {e}")
        return False


async def get_pick_details(supabase, pick_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information about a specific pick."""
    try:
        response = await execute_async(supabase.table('draft_picks').select(
            '*, seasons(name), current_owner_team:current_owner_team_id(code, name), original_team:original_team_id(code, name)'
        ).eq('id', pick_id).limit(1))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Failed to get pick details: {e}")
        return None


async def get_picks_by_id(supabase, pick_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Details of several picks in one query, keyed by pick id."""
    if not pick_ids:
        return {}
    response = await execute_async(supabase.table('draft_picks').select(
        '*, seasons(name), current_owner_team:current_owner_team_id(code, name), original_team:original_team_id(code, name)'
    ).in_('id', pick_ids))
    return {pick['id']: pick for pick in (response.data or [])}


async def pick_ids_in_pending_trades(supabase, pick_ids: List[str]) -> Set[str]:
    """Which of these picks are already part of a pending trade (two queries, however many picks)."""
    if not pick_ids:
        return set()
    pending_proposals = await execute_async(supabase.table('trade_proposals').select('id').eq('status', 'pending'))
    proposal_ids = [p['id'] for p in (pending_proposals.data or [])]
    if not proposal_ids:
        return set()
    assets = await execute_async(
        supabase.table('trade_assets').select('draft_pick_id')
        .in_('draft_pick_id', pick_ids).in_('proposal_id', proposal_ids)
    )
    return {asset['draft_pick_id'] for asset in (assets.data or [])}


def format_pick(pick: Dict[str, Any]) -> str:
    """Format a pick for display: 'R1.3'"""
    return f"R{pick.get('round', '?')}.{pick.get('overall', '?')}"


def create_pick_label(pick: Dict[str, Any]) -> str:
    """Create a Discord select option label for a pick."""
    return f"#{pick.get('overall', '?')} - {format_pick(pick)}"


async def validate_picks_for_trade(
    supabase,
    pick_ids: List[str],
    owner_team_id: str,
    season_id: str
) -> tuple[bool, str]:
    """
    Validate that all picks:
    1. Exist and belong to the season
    2. Belong to the owner
    3. Are from a division that runs a draft
    4. Aren't already in pending trades
    """
    try:
        picks = await get_picks_by_id(supabase, pick_ids)
        in_pending_trade = await pick_ids_in_pending_trades(supabase, pick_ids)
        for pick_id in pick_ids:
            pick = picks.get(pick_id)
            if not pick:
                return False, f"❌ Pick not found: {pick_id}"

            if pick.get('season_id') != season_id:
                return False, f"❌ Pick #{pick.get('overall', '?')} is from a different season"

            if not picks_enabled_for_division(pick.get('division_id')):
                return False, "❌ Draft picks are turned off for that division"

            if pick.get('current_owner_team_id') != owner_team_id:
                return False, f"❌ Your team doesn't own pick #{pick.get('overall', '?')}"

            if pick_id in in_pending_trade:
                return False, f"❌ Pick #{pick.get('overall', '?')} is already in a pending trade"

        return True, "✅ All picks valid"
    except Exception as e:
        logger.error(f"Failed to validate picks: {e}")
        return False, f"❌ Validation error: {str(e)[:50]}"
