"""Utilities for trade management and display."""

from typing import Any, Dict, List, Optional
from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
import logging

logger = logging.getLogger(__name__)


async def enrich_trade_assets(
    assets: List[Dict[str, Any]],
    supabase: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Enrich trade assets with labels by looking up players and picks."""
    if not supabase:
        supabase = get_supabase()
    
    if not supabase:
        return assets
    
    enriched = []
    cache = TeamPlayerCache()
    
    for asset in assets:
        enriched_asset = asset.copy()
        
        if asset.get('asset_type') == 'player' and asset.get('player_id'):
            # Look up player name
            try:
                player = cache.get_player_by_id(asset['player_id'])
                if player:
                    enriched_asset['label'] = player.get('display_name', 'Unknown Player')
                else:
                    enriched_asset['label'] = 'Unknown Player'
            except Exception as e:
                logger.warning(f"Failed to enrich player asset: {e}")
                enriched_asset['label'] = 'Unknown Player'
                
        elif asset.get('asset_type') == 'pick' and asset.get('draft_pick_id'):
            # Look up pick info
            try:
                response = supabase.table('draft_picks').select('*').eq('id', asset['draft_pick_id']).execute()
                if response.data and len(response.data) > 0:
                    pick = response.data[0]
                    enriched_asset['label'] = f"R{pick.get('round', '?')}P{pick.get('overall', '?'):02d}"
                else:
                    enriched_asset['label'] = 'Unknown Pick'
            except Exception as e:
                logger.warning(f"Failed to enrich pick asset: {e}")
                enriched_asset['label'] = 'Unknown Pick'
        else:
            enriched_asset['label'] = enriched_asset.get('label', 'Unknown Asset')
        
        enriched.append(enriched_asset)
    
    return enriched


async def can_trade_picks(team1_division_id: str, team2_division_id: str, supabase: Optional[Any] = None) -> bool:
    """
    Determine if draft picks can be traded between two teams.
    
    Rules:
    - Different divisions: NO picks
    - Same division + NBL: YES picks
    - Same division + not NBL: NO picks
    """
    if not supabase:
        supabase = get_supabase()
    
    if not supabase:
        return False
    
    # If different divisions, no picks
    if team1_division_id != team2_division_id:
        return False
    
    # Get division info
    try:
        response = supabase.table('divisions').select('league_type').eq('id', team1_division_id).execute()
        if response.data and len(response.data) > 0:
            division = response.data[0]
            league_type = division.get('league_type', '').upper()
            # Only NBL can trade picks within same division
            return league_type == 'NBL'
    except Exception as e:
        logger.warning(f"Failed to check division for pick trading: {e}")
    
    return False


async def get_available_trade_assets(
    team_id: str,
    season_id: str,
    allow_picks: bool = True,
    supabase: Optional[Any] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Get available players and picks for a team to trade."""
    if not supabase:
        supabase = get_supabase()
    
    if not supabase:
        return {'players': [], 'picks': []}
    
    result = {'players': [], 'picks': []}
    
    try:
        # Get active rosters for this team
        rosters = await queries.get_active_rosters(supabase, season_id)
        players = [r for r in rosters if r.get('team_id') == team_id]
        
        result['players'] = [
            {
                'id': f"player_{p['player_id']}",
                'player_id': p['player_id'],
                'label': p.get('players', {}).get('display_name', 'Unknown'),
                'type': 'player',
            }
            for p in players
        ]
        
        # Get draft picks if allowed
        if allow_picks:
            try:
                from db.transactions_repository import TransactionsRepository
                repo = TransactionsRepository(supabase)
                picks = repo.list_draft_picks(season_id)
                team_picks = [p for p in picks if p.get('current_owner_team_id') == team_id]
                
                result['picks'] = [
                    {
                        'id': f"pick_{p['id']}",
                        'draft_pick_id': p['id'],
                        'label': f"R{p.get('round', '?')}P{p.get('overall', '?'):02d}",
                        'type': 'pick',
                    }
                    for p in team_picks
                ]
            except Exception as e:
                logger.warning(f"Failed to fetch picks: {e}")
    
    except Exception as e:
        logger.warning(f"Failed to get trade assets: {e}")
    
    return result
