"""
Common Supabase queries and database operations
Keep as pure functions to avoid circular imports
"""

import logging
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


async def get_active_season(supabase, division_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get the current active season, optionally for a specific division"""
    try:
        query = supabase.table('seasons').select('*').eq('active', True)
        if division_id:
            query = query.eq('division_id', division_id)
        response = query.limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching active season: {e}")
        return None


async def get_all_active_seasons(supabase) -> List[Dict[str, Any]]:
    """Get all active seasons across all divisions"""
    try:
        response = supabase.table('seasons').select(
            '*, divisions(id, league_type, name)'
        ).eq('active', True).execute()
        
        # Enrich seasons with division league type and name
        if response.data:
            for season in response.data:
                division = season.get('divisions', {})
                if isinstance(division, dict):
                    season['league_type'] = division.get('league_type')
                    season['league_name'] = division.get('name')
        
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching all active seasons: {e}")
        return []


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


async def get_blocked_teams_for_guild(supabase, guild_id: int) -> List[str]:
    """Fetch blocked team IDs for a guild from transaction_blocker_entries."""
    if not guild_id:
        return []

    try:
        response = (
            supabase.table('transaction_blocker_entries')
            .select('team_id')
            .eq('guild_id', guild_id)
            .eq('blocked', True)
            .execute()
        )
        data = response.data or []
        return [entry.get('team_id') for entry in data if entry.get('team_id')]
    except Exception as e:
        logger.error(f"Error fetching blocked teams for guild {guild_id}: {e}")
        return []


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


async def get_standings(supabase, season_id: str, league_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get team standings from view_standings with team emoji"""
    try:
        query = supabase.table('view_standings').select('*, teams(emoji, code)').eq('season_id', season_id)
        if league_type:
            query = query.eq('league_type', league_type)
        response = query.execute()
        
        # Enrich standings data with team emoji from joined teams
        if response.data:
            for row in response.data:
                teams_data = row.get('teams') if isinstance(row.get('teams'), dict) else None
                if teams_data:
                    row['emoji'] = teams_data.get('emoji', '')
                    row.setdefault('code', teams_data.get('code'))
        
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


# =====================================================================
# Stats Functions (wrapping SQL functions from db/migrations/20251108_stats_functions.sql)
# =====================================================================

async def get_player_averages(supabase, player_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get player per-game averages for a season"""
    try:
        response = supabase.rpc('get_player_averages', {
            'p_player_id': player_id,
            'p_season_id': season_id
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player averages: {e}")
        return None


async def get_player_totals(supabase, player_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get player season totals"""
    try:
        response = supabase.rpc('get_player_totals', {
            'p_player_id': player_id,
            'p_season_id': season_id
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player totals: {e}")
        return None


async def get_team_totals(supabase, team_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get team season totals"""
    try:
        response = supabase.rpc('get_team_totals', {
            'p_team_id': team_id,
            'p_season_id': season_id
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team totals: {e}")
        return None


async def get_team_averages(supabase, team_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get team per-game averages"""
    try:
        response = supabase.rpc('get_team_averages', {
            'p_team_id': team_id,
            'p_season_id': season_id
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team averages: {e}")
        return None


async def get_leaderboard(supabase, metric: str, season_id: str, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Get leaderboard for a specific metric with pagination support.
    
    Args:
        supabase: Supabase client
        metric: Metric name (points, assists, goals, etc.)
        season_id: Season ID filter
        limit: Maximum results to return (default 10, max 100)
        offset: Starting position for pagination (default 0)
    """
    try:
        limit = min(limit, 100)  # Cap at 100 per request
        response = supabase.rpc('get_leaderboard', {
            'p_metric': metric,
            'p_season_id': season_id,
            'p_limit': limit,
            'p_offset': offset
        }).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        return []


async def get_player_history(supabase, player_id: str, season_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Get player game-by-game history for a season with pagination support.
    
    Args:
        supabase: Supabase client
        player_id: Player ID to fetch history for
        season_id: Season ID filter
        limit: Maximum results to return (default 50, max 100)
        offset: Starting position for pagination (default 0)
    """
    try:
        limit = min(limit, 100)  # Cap at 100 per request
        response = supabase.rpc('get_player_history', {
            'p_player_id': player_id,
            'p_season_id': season_id,
            'p_limit': limit,
            'p_offset': offset
        }).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching player history: {e}")
        return []


async def compare_players(supabase, player_a_id: str, player_b_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Compare two players across a season"""
    try:
        response = supabase.rpc('compare_players', {
            'p_player_a_id': player_a_id,
            'p_player_b_id': player_b_id,
            'p_season_id': season_id
        }).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error comparing players: {e}")
        return []


async def get_season_summary(supabase, season_id: str) -> Optional[Dict[str, Any]]:
    """Get season summary statistics"""
    try:
        response = supabase.rpc('get_season_summary', {
            'p_season_id': season_id
        }).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching season summary: {e}")
        return None


async def get_roster_for_team_season(supabase, team_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Get roster for a specific team in a season (active or historical)"""
    try:
        response = supabase.table('rosters').select(
            'id, player_id, team_id, season_id, joined_at, left_at, '
            'players(id, display_name, discord_id)'
        ).eq('team_id', team_id).eq('season_id', season_id).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching roster for team {team_id} in season {season_id}: {e}")
        return []


async def get_matches_for_season(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get all completed matches for a season with team info."""
    try:
        response = supabase.table('matches').select(
            'id, team1_id, team2_id, team1_score, team2_score, played_at, status, '
            'team1:team1_id(id, code, name, emoji), '
            'team2:team2_id(id, code, name, emoji)'
        ).eq('season_id', season_id).eq('status', 'final').order('played_at', desc=False).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching matches for season {season_id}: {e}")
        return []


async def get_all_matches_for_season(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get ALL matches for a season (played and scheduled) with team info."""
    try:
        response = supabase.table('matches').select(
            'id, team1_id, team2_id, team1_score, team2_score, played_at, status, '
            'team1:team1_id(id, code, name, emoji), '
            'team2:team2_id(id, code, name, emoji)'
        ).eq('season_id', season_id).order('played_at', desc=False).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching all matches for season {season_id}: {e}")
        return []
