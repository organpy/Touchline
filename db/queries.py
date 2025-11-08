"""
Common Supabase queries and database operations
Keep as pure functions to avoid circular imports
"""

import logging
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


async def get_active_season(supabase) -> Optional[Dict[str, Any]]:
    """Get the current active season"""
    try:
        response = supabase.table('seasons').select('*').eq('active', True).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching active season: {e}")
        return None


async def get_division_by_type(supabase, league_type: str = 'NBL') -> Optional[Dict[str, Any]]:
    """Get division by league type (NBL, MBL, NCBA, etc.)"""
    try:
        response = supabase.table('divisions').select('*').eq('league_type', league_type).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching division {league_type}: {e}")
        return None


async def get_all_teams(supabase, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all teams, optionally filtered by division"""
    try:
        query = supabase.table('teams').select('*')
        if division_id:
            query = query.eq('division_id', division_id)
        response = query.execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching teams: {e}")
        return []


async def get_active_rosters(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get all active rosters for a season (left_at IS NULL)"""
    try:
        response = supabase.table('rosters').select(
            'id, player_id, team_id, season_id, joined_at, '
            'players(id, display_name, discord_id), '
            'teams(id, code, name, emoji)'
        ).eq('season_id', season_id).is_('left_at', 'null').execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching active rosters: {e}")
        return []


async def get_team_by_code(supabase, code: str) -> Optional[Dict[str, Any]]:
    """Get team by code"""
    try:
        response = supabase.table('teams').select('*').eq('code', code).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team {code}: {e}")
        return None


async def get_team_by_owner(supabase, owner_discord_id: int) -> Optional[Dict[str, Any]]:
    """Get the team managed by a Discord user."""
    try:
        response = (
            supabase
            .table('teams')
            .select('*')
            .eq('owner_discord_id', owner_discord_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team for owner {owner_discord_id}: {e}")
        return None


async def get_player_by_discord_id(supabase, discord_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a player row by Discord account id."""
    try:
        response = (
            supabase
            .table('players')
            .select('*')
            .eq('discord_id', discord_id)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player by discord id {discord_id}: {e}")
        return None


async def get_player_by_name(supabase, display_name: str) -> Optional[Dict[str, Any]]:
    """Get player by display name (case-insensitive)"""
    try:
        response = supabase.table('players').select('*').ilike('display_name', display_name).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player {display_name}: {e}")
        return None


async def get_or_create_player(supabase, display_name: str, discord_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Get player by name or create if doesn't exist"""
    try:
        # Try to get existing
        player = await get_player_by_name(supabase, display_name)
        if player:
            return player

        # Create new
        insert_data = {'display_name': display_name}
        if discord_id:
            insert_data['discord_id'] = discord_id

        response = supabase.table('players').insert(insert_data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error getting/creating player {display_name}: {e}")
        return None


async def insert_match(supabase, match_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a match row"""
    try:
        response = supabase.table('matches').insert(match_data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error inserting match: {e}")
        return None


async def insert_player_stats_batch(supabase, stats_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Insert multiple player_stats rows"""
    try:
        if not stats_list:
            return []
        response = supabase.table('player_stats').insert(stats_list).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error inserting player stats: {e}")
        return []


async def get_standings(supabase, season_id: str, league_type: str = 'NBL') -> List[Dict[str, Any]]:
    """Get team standings from view_standings"""
    try:
        response = supabase.table('view_standings').select('*').eq('season_id', season_id).eq('league_type', league_type).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching standings: {e}")
        return []


async def get_player_stats_for_season(supabase, player_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Get all player stats for a season"""
    try:
        response = supabase.table('player_stats').select(
            '*, matches(id, played_at, team1_score, team2_score)'
        ).eq('player_id', player_id).eq('matches.season_id', season_id).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching player stats: {e}")
        return []
