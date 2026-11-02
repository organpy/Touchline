"""
Common Supabase queries and database operations.

Seasons are league-wide; which division a team plays in is per season (``season_teams``).
Team rows returned here are enriched through ``LeagueCache`` with their active-season
division (``division_id``, ``league_type`` = division code, ``league_name``).
"""

import logging
from typing import Optional, Dict, List, Any

from services.division_settings import DivisionSettings
from utils.league_cache import LeagueCache, enrich_team
from .db_utils import execute_async

logger = logging.getLogger(__name__)


def _season_with_division(season: Optional[Dict[str, Any]], division_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if season is None or not division_id:
        return season
    season = dict(season)
    division = LeagueCache.division(division_id) or {}
    season["division_id"] = division_id
    season["league_type"] = division.get("code")
    season["league_name"] = division.get("name")
    return season


async def get_active_season(supabase, division_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The league's active season. ``division_id`` is only attached to the returned dict for
    callers that display it; seasons themselves span every division."""
    try:
        response = await execute_async(supabase.table('seasons').select('*').eq('active', True).limit(1))
        season = response.data[0] if response.data else None
        return _season_with_division(season, division_id)
    except Exception as e:
        logger.error(f"Error fetching active season: {e}")
        return None


async def get_matches_between_teams(
    supabase,
    season_id: str,
    team1_id: str,
    team2_id: str,
    stage: Optional[str] = 'regular',
) -> List[Dict[str, Any]]:
    """Final matches between two teams in a season (both orders). ``stage=None`` for every stage."""
    try:
        query = (
            supabase
            .table('matches')
            .select('*')
            .eq('season_id', season_id)
            .eq('status', 'final')
            .or_(f"and(team1_id.eq.{team1_id},team2_id.eq.{team2_id}),and(team1_id.eq.{team2_id},team2_id.eq.{team1_id})")
        )
        if stage:
            query = query.eq('stage', stage)
        response = await execute_async(query.order('played_at', desc=True))
        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching matches between teams: {e}")
        return []


async def get_team_season_division(supabase, season_id: str, team_id: str) -> Optional[Dict[str, Any]]:
    """The ``season_teams`` row (division_id, group_id) for a team in a season."""
    cached = LeagueCache.assignment(team_id, season_id)
    if cached:
        return cached
    try:
        response = await execute_async(
            supabase.table('season_teams').select('season_id, team_id, division_id, group_id')
            .eq('season_id', season_id).eq('team_id', team_id).limit(1)
        )
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching season division for team {team_id}: {e}")
        return None


async def get_team_recent_matches(
    supabase,
    season_id: str,
    team_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Get recent matches for a team in a season, most recent first."""
    try:
        response = await execute_async(
            supabase
            .table('matches')
            .select('id, team1_id, team2_id, team1_score, team2_score, status, stage, played_at')
            .eq('season_id', season_id)
            .eq('status', 'final')
            .or_(f"team1_id.eq.{team_id},team2_id.eq.{team_id}")
            .order('played_at', desc=True)
            .limit(limit)
        )
        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching team recent matches: {e}")
        return []


async def get_all_active_seasons(supabase) -> List[Dict[str, Any]]:
    """The active season as a list (there is at most one)."""
    season = await get_active_season(supabase)
    return [season] if season else []


async def get_all_seasons(supabase) -> List[Dict[str, Any]]:
    """Every season, newest first."""
    try:
        response = await execute_async(supabase.table('seasons').select('*').order('number', desc=True))
        return response.data or []
    except Exception as e:
        logger.error(f"Error fetching all seasons: {e}")
        return []


def _enrich_division(division: Dict[str, Any]) -> Dict[str, Any]:
    division = dict(division)
    division['league_type'] = division.get('code')
    return division


async def get_all_divisions(supabase, include_archived: bool = False) -> List[Dict[str, Any]]:
    """Divisions in tier order (1 = top)."""
    try:
        query = supabase.table('divisions').select('*')
        if not include_archived:
            query = query.is_('archived_at', 'null')
        response = await execute_async(query.order('tier'))
        return [_enrich_division(d) for d in (response.data or [])]
    except Exception as e:
        logger.error(f"Error fetching divisions: {e}")
        return []


async def get_division_by_code(supabase, code: str) -> Optional[Dict[str, Any]]:
    """Division by code (case-insensitive, e.g. ``D1``)."""
    if not code:
        return None
    cached = LeagueCache.find_division(code)
    if cached and (cached.get('code') or '').lower() == code.strip().lower():
        return cached
    try:
        response = await execute_async(
            supabase.table('divisions').select('*').eq('code', code.strip().upper()).limit(1)
        )
        return _enrich_division(response.data[0]) if response.data else None
    except Exception as e:
        logger.error(f"Error fetching division {code}: {e}")
        return None


async def get_division_by_id(supabase, division_id: str) -> Optional[Dict[str, Any]]:
    """Division by ID."""
    cached = LeagueCache.division(division_id)
    if cached:
        return cached
    try:
        response = await execute_async(supabase.table('divisions').select('*').eq('id', division_id).limit(1))
        return _enrich_division(response.data[0]) if response.data else None
    except Exception as e:
        logger.error(f"Error fetching division {division_id}: {e}")
        return None


async def get_all_teams(supabase, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Active (non-archived) teams, sorted by name. With ``division_id``: only the teams playing in
    that division in the active season."""
    try:
        if division_id:
            season = await get_active_season(supabase)
            if not season:
                return []
            response = await execute_async(
                supabase.table('season_teams').select('teams(*)')
                .eq('season_id', season['id']).eq('division_id', division_id)
            )
            rows = [r.get('teams') for r in (response.data or []) if r.get('teams')]
        else:
            response = await execute_async(supabase.table('teams').select('*').is_('archived_at', 'null'))
            rows = response.data or []
        teams = [enrich_team(t) for t in rows if not t.get('archived_at')]
        return sorted(teams, key=lambda t: (t.get('name') or '').lower())
    except Exception as e:
        logger.error(f"Error fetching teams: {e}")
        return []


async def get_active_rosters(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get all active rosters for a season (left_at IS NULL)"""
    try:
        response = await execute_async(supabase.table('rosters').select(
            'id, player_id, team_id, season_id, joined_at, '
            'players(id, display_name, discord_id), '
            'teams(id, code, name, emoji)'
        ).eq('season_id', season_id).is_('left_at', 'null'))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching active rosters: {e}")
        return []


async def get_team_by_code(supabase, code: str) -> Optional[Dict[str, Any]]:
    """Team by code (codes are lowercase slugs)."""
    if not code:
        return None
    try:
        response = await execute_async(
            supabase.table('teams').select('*').eq('code', code.strip().lower()).limit(1)
        )
        return enrich_team(response.data[0]) if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team {code}: {e}")
        return None


async def get_team_by_owner(supabase, owner_discord_id: int) -> Optional[Dict[str, Any]]:
    """The active team managed by a Discord user (an owner runs at most one team)."""
    try:
        response = await execute_async(
            supabase
            .table('teams')
            .select('*')
            .eq('owner_discord_id', owner_discord_id)
            .is_('archived_at', 'null')
            .limit(1)
        )
        return enrich_team(response.data[0]) if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team for owner {owner_discord_id}: {e}")
        return None


async def get_teams_by_owner(supabase, owner_discord_id: int) -> List[Dict[str, Any]]:
    """The owner's team as a list (empty or one item), kept for callers that loop."""
    team = await get_team_by_owner(supabase, owner_discord_id)
    return [team] if team else []


async def get_player_by_discord_id(supabase, discord_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a player row by Discord account id."""
    try:
        response = await execute_async(
            supabase
            .table('players')
            .select('*')
            .eq('discord_id', discord_id)
            .limit(1)
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
        response = await execute_async(
            supabase.table('transaction_blocker_entries')
            .select('team_id')
            .eq('guild_id', guild_id)
            .eq('blocked', True)
        )
        data = response.data or []
        return [entry.get('team_id') for entry in data if entry.get('team_id')]
    except Exception as e:
        logger.error(f"Error fetching blocked teams for guild {guild_id}: {e}")
        return []


async def get_player_by_name(supabase, display_name: str) -> Optional[Dict[str, Any]]:
    """Get player by display name (case-insensitive)"""
    try:
        response = await execute_async(supabase.table('players').select('*').ilike('display_name', display_name).limit(1))
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

        response = await execute_async(supabase.table('players').insert(insert_data))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error getting/creating player {display_name}: {e}")
        return None


async def insert_match(supabase, match_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a match row"""
    try:
        response = await execute_async(supabase.table('matches').insert(match_data))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error inserting match: {e}")
        return None


async def insert_player_stats_batch(supabase, stats_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Insert multiple player_stats rows"""
    try:
        if not stats_list:
            return []
        response = await execute_async(supabase.table('player_stats').insert(stats_list))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error inserting player stats: {e}")
        return []


async def get_standings(
    supabase,
    season_id: str,
    division_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Ranked standings rows for a season (optionally one division), best first.

    Each row has ``team_id``, ``team_name``, ``code``, ``emoji``, ``division_id``, ``league_type``,
    ``gp``, ``wins``, ``losses``, ``points_for``, ``points_against``, ``point_diff``,
    ``competition_points`` and ``rank`` (within the division).
    """
    try:
        query = supabase.table('team_records').select('*, teams(name, code, emoji, color, logo_url)').eq('season_id', season_id)
        if division_id:
            query = query.eq('division_id', division_id)
        response = await execute_async(query)
        rows = []
        for record in response.data or []:
            team = record.get('teams') if isinstance(record.get('teams'), dict) else {}
            division = LeagueCache.division(record.get('division_id')) or {}
            settings = DivisionSettings.from_division(division)
            pf = record.get('points_for') or 0
            pa = record.get('points_against') or 0
            wins = record.get('wins') or 0
            rows.append({
                **record,
                'team_name': team.get('name'),
                'name': team.get('name'),
                'code': team.get('code'),
                'emoji': team.get('emoji') or '',
                'color': team.get('color'),
                'logo_url': team.get('logo_url'),
                'league_type': division.get('code'),
                'tier': division.get('tier') or 0,
                'gp': record.get('games_played') or 0,
                'point_diff': pf - pa,
                'competition_points': settings.competition_points(wins=wins, points_for=pf),
            })
        rows.sort(key=lambda r: (
            r['tier'], -r['competition_points'], -r['point_diff'], -(r.get('points_for') or 0),
            -(r.get('wins') or 0), (r.get('team_name') or '').lower(),
        ))
        ranks: Dict[str, int] = {}
        for row in rows:
            ranks[row['division_id']] = ranks.get(row['division_id'], 0) + 1
            row['rank'] = ranks[row['division_id']]
        return rows
    except Exception as e:
        logger.error(f"Error fetching standings: {e}")
        return []


async def get_player_stats_for_season(supabase, player_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Get all player stats for a season"""
    try:
        response = await execute_async(supabase.table('player_stats').select(
            '*, matches!inner(id, season_id, played_at, team1_score, team2_score, status)'
        ).eq('player_id', player_id).eq('matches.season_id', season_id).eq('matches.status', 'final'))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching player stats: {e}")
        return []


# =====================================================================
# Stats Functions (wrapping SQL functions from db/migrations/init.sql)
# =====================================================================

async def get_player_averages(supabase, player_id: str, season_id: str,
                              division_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get player per-game averages for a season (optionally only games for a division's teams)"""
    try:
        response = await execute_async(supabase.rpc('get_player_averages', {
            'p_player_id': player_id,
            'p_season_id': season_id,
            'p_division_id': division_id,
        }))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player averages: {e}")
        return None


async def get_player_totals(supabase, player_id: str, season_id: str,
                            division_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get player season totals (optionally only games for a division's teams)"""
    try:
        response = await execute_async(supabase.rpc('get_player_totals', {
            'p_player_id': player_id,
            'p_season_id': season_id,
            'p_division_id': division_id,
        }))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching player totals: {e}")
        return None


async def get_team_totals(supabase, team_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get team season totals"""
    try:
        response = await execute_async(supabase.rpc('get_team_totals', {
            'p_team_id': team_id,
            'p_season_id': season_id
        }))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team totals: {e}")
        return None


async def get_team_averages(supabase, team_id: str, season_id: str) -> Optional[Dict[str, Any]]:
    """Get team per-game averages"""
    try:
        response = await execute_async(supabase.rpc('get_team_averages', {
            'p_team_id': team_id,
            'p_season_id': season_id
        }))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching team averages: {e}")
        return None


async def get_leaderboard(supabase, metric: str, season_id: str, limit: int = 10, offset: int = 0,
                          division_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get leaderboard for a specific metric with pagination support.
    
    Args:
        supabase: Supabase client
        metric: Metric name (points, assists, goals, etc.)
        season_id: Season ID filter
        limit: Maximum results to return (default 10, max 100)
        offset: Starting position for pagination (default 0)
        division_id: Only players whose team was in this division
    """
    try:
        limit = min(limit, 100)  # Cap at 100 per request
        response = await execute_async(supabase.rpc('get_leaderboard', {
            'p_metric': metric,
            'p_season_id': season_id,
            'p_limit': limit,
            'p_offset': offset,
            'p_division_id': division_id,
        }))
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
        response = await execute_async(supabase.rpc('get_player_history', {
            'p_player_id': player_id,
            'p_season_id': season_id,
            'p_limit': limit,
            'p_offset': offset
        }))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching player history: {e}")
        return []


async def compare_players(supabase, player_a_id: str, player_b_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Compare two players across a season"""
    try:
        response = await execute_async(supabase.rpc('compare_players', {
            'p_player_a_id': player_a_id,
            'p_player_b_id': player_b_id,
            'p_season_id': season_id
        }))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error comparing players: {e}")
        return []


async def get_season_summary(supabase, season_id: str, division_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get season summary statistics (optionally for one division)"""
    try:
        response = await execute_async(supabase.rpc('get_season_summary', {
            'p_season_id': season_id,
            'p_division_id': division_id,
        }))
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error fetching season summary: {e}")
        return None


async def get_roster_for_team_season(supabase, team_id: str, season_id: str) -> List[Dict[str, Any]]:
    """Get roster for a specific team in a season (active or historical)"""
    try:
        response = await execute_async(supabase.table('rosters').select(
            'id, player_id, team_id, season_id, joined_at, left_at, '
            'players(id, display_name, discord_id)'
        ).eq('team_id', team_id).eq('season_id', season_id))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching roster for team {team_id} in season {season_id}: {e}")
        return []


async def get_matches_for_season(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get all completed matches for a season with team info."""
    try:
        response = await execute_async(supabase.table('matches').select(
            'id, team1_id, team2_id, team1_score, team2_score, played_at, status, stage, division_id, series_id, '
            'team1:team1_id(id, code, name, emoji), '
            'team2:team2_id(id, code, name, emoji)'
        ).eq('season_id', season_id).eq('status', 'final').order('played_at', desc=False))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching matches for season {season_id}: {e}")
        return []


async def get_all_matches_for_season(supabase, season_id: str) -> List[Dict[str, Any]]:
    """Get every non-void match for a season (all stages) with team info."""
    try:
        response = await execute_async(supabase.table('matches').select(
            'id, team1_id, team2_id, team1_score, team2_score, played_at, status, stage, division_id, series_id, '
            'team1:team1_id(id, code, name, emoji), '
            'team2:team2_id(id, code, name, emoji)'
        ).eq('season_id', season_id).neq('status', 'void').order('played_at', desc=False))
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error fetching all matches for season {season_id}: {e}")
        return []
