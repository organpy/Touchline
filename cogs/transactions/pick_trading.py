"""
Draft Pick Trading - NBA-style pick trading system restricted to NBL division.
Handles creating, proposing, and executing pick trades.
"""

import logging
from typing import Optional, Dict, Any, List

from db import queries
from db.db_utils import execute_async

logger = logging.getLogger(__name__)

# NBL is the "first division" - primary competitive league
NBL_LEAGUE_TYPE = "NBL"


async def get_nbl_division(supabase) -> Optional[Dict[str, Any]]:
    """Get the NBL division."""
    try:
        response = await execute_async(supabase.table('divisions').select('*').eq('league_type', NBL_LEAGUE_TYPE).limit(1))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Failed to get NBL division: {e}")
        return None


async def validate_nbl_season(supabase, season_id: str) -> bool:
    """Verify that a season belongs to NBL division."""
    try:
        response = await execute_async(supabase.table('seasons').select('division_id').eq('id', season_id).limit(1))
        if not response.data:
            return False
        
        division_id = response.data[0]['division_id']
        nbl_div = await get_nbl_division(supabase)
        return nbl_div and nbl_div['id'] == division_id
    except Exception as e:
        logger.error(f"Failed to validate NBL season: {e}")
        return False


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
            'updated_at': 'now()'
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


def format_pick(pick: Dict[str, Any]) -> str:
    """Format a pick for display: 'Round X, Pick Y'"""
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
    1. Exist
    2. Belong to the owner
    3. Are from NBL season
    4. Aren't already in pending trades
    """
    try:
        # Validate season is NBL
        if not await validate_nbl_season(supabase, season_id):
            return False, "❌ Draft picks must be from NBL division"
        
        # Validate each pick
        for pick_id in pick_ids:
            pick = await get_pick_details(supabase, pick_id)
            if not pick:
                return False, f"❌ Pick not found: {pick_id}"
            
            if pick.get('current_owner_team_id') != owner_team_id:
                return False, f"❌ Your team doesn't own pick #{pick.get('overall', '?')}"
            
            # Check if pick is in pending trade
            pending = await execute_async(supabase.table('trade_assets').select('id').eq(
                'draft_pick_id', pick_id
            ).in_('proposal_id', [
                p['id'] for p in ((await execute_async(supabase.table('trade_proposals').select('id').eq(
                    'status', 'pending'
                ))).data or [])
            ]).limit(1))
            
            if pending.data:
                return False, f"❌ Pick #{pick.get('overall', '?')} is already in a pending trade"
        
        return True, "✅ All picks valid"
    except Exception as e:
        logger.error(f"Failed to validate picks: {e}")
        return False, f"❌ Validation error: {str(e)[:50]}"
