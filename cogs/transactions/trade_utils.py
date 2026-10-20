"""Utilities for trade management and display."""

from typing import Any, Dict, List, Optional
from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
import logging
from db.db_utils import execute_async
from cogs.transactions.pick_trading import picks_enabled_for_division
from utils.league_cache import LeagueCache

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
                player = cache.get_player(asset['player_id'])
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
                response = await execute_async(supabase.table('draft_picks').select('*').eq('id', asset['draft_pick_id']))
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
    Draft picks can change hands only between two teams of the same division, and only when that
    division runs a draft (``picks_enabled``).
    """
    if not team1_division_id or team1_division_id != team2_division_id:
        return False
    return picks_enabled_for_division(team1_division_id)


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
                division = LeagueCache.team_division(team_id, season_id)
                picks = await repo.list_draft_picks(season_id, division['id']) if division else []
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
