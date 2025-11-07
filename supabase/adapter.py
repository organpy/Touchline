"""Supabase/Postgres adapter for multi-league bot (NBL, MBL, NCBA)

This adapter provides a thin abstraction layer between Discord bot commands
and the Supabase Postgres database. It implements methods for:
- Reading standings (materialized views)
- Recording matches and player stats
- Fetching player totals/averages
- Managing team rosters and transactions

Environment variables:
- DATABASE_URL: Postgres connection string (e.g., from Supabase)

Dependencies:
- psycopg2-binary: pip install psycopg2-binary

Usage:
    from supabase.adapter import get_leaderboard, record_match
    
    standings = get_leaderboard(league='NBL', season_id=season_id)
    match_id = record_match(payload)
"""

from typing import List, Dict, Any, Optional
import os
import psycopg2
import psycopg2.extras
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.warning("DATABASE_URL not set; adapter will not connect until configured")


def get_conn():
    """Get a new database connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return psycopg2.connect(DATABASE_URL)


# ============================================================================
# LEADERBOARD QUERIES
# ============================================================================

def get_leaderboard(league: str, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Get leaderboard standings for a league.
    
    Args:
        league: 'NBL', 'MBL', or 'NCBA'
        limit: Max number of teams to return (default 25)
    
    Returns:
        List of team standings (ordered by point/goal diff DESC)
    """
    if league.upper() in ['NBL', 'NCBA']:
        view = 'view_standings_nbl_ncba'
        order_cols = 'point_diff DESC, points_for DESC'
    elif league.upper() == 'MBL':
        view = 'view_standings_mbl'
        order_cols = 'goal_diff DESC, goals_for DESC'
    else:
        raise ValueError(f"Unknown league: {league}")
    
    sql = f"""
    SELECT season_id, division_id, league_type, team_id, code, name, gp, wins, ties, losses
    FROM {view}
    WHERE league_type = %s
    ORDER BY {order_cols}
    LIMIT %s
    """
    
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (league.upper(), limit))
            return [dict(row) for row in cur.fetchall()]


def get_team_info(league: str, team_code: str, season_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get team info and roster for a given season.
    
    Args:
        league: 'NBL', 'MBL', or 'NCBA'
        team_code: Team code (e.g., 'pandas')
        season_id: Season UUID; if None, uses active season
    
    Returns:
        Dict with 'team' (basic info) and 'roster' (list of players)
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get season_id if not provided
            if not season_id:
                cur.execute('SELECT id FROM seasons WHERE active = true LIMIT 1')
                season_row = cur.fetchone()
                if not season_row:
                    logger.error("No active season found")
                    return {}
                season_id = season_row['id']
            
            # Get team
            cur.execute(
                """SELECT t.id, t.code, t.name, t.emoji, t.owner_discord_id
                   FROM teams t
                   JOIN divisions d ON d.id = t.division_id
                   WHERE t.code = %s AND d.league_type = %s""",
                (team_code, league.upper())
            )
            team_row = cur.fetchone()
            if not team_row:
                logger.error(f"Team {team_code} not found in league {league}")
                return {}
            
            team_id = team_row['id']
            
            # Get roster for season
            cur.execute(
                """SELECT p.id, p.display_name, p.discord_id, r.role, r.joined_at, r.left_at
                   FROM rosters r
                   JOIN players p ON p.id = r.player_id
                   WHERE r.season_id = %s AND r.team_id = %s AND r.left_at IS NULL
                   ORDER BY p.display_name""",
                (season_id, team_id)
            )
            roster = [dict(row) for row in cur.fetchall()]
            
            return {
                'team': dict(team_row),
                'roster': roster,
                'season_id': str(season_id)
            }


# ============================================================================
# PLAYER STATS QUERIES
# ============================================================================

def get_player_totals(league: str, stat: str = 'goals', limit: int = 50, season_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get player totals for a league (e.g., top goal scorers).
    
    Args:
        league: 'NBL', 'MBL', or 'NCBA'
        stat: 'goals', 'assists', 'saves', 'blocks', 'points' (NBL/NCBA only)
        limit: Max players to return
        season_id: Season UUID; if None, uses active season
    
    Returns:
        List of players with total stats
    """
    stat_col = stat.lower()
    if stat_col not in ['goals', 'own_goals', 'assists', 'saves', 'blocks', 'powerup_plays', 'points']:
        raise ValueError(f"Unknown stat: {stat}")
    
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not season_id:
                cur.execute('SELECT id FROM seasons WHERE active = true LIMIT 1')
                season_row = cur.fetchone()
                if not season_row:
                    return []
                season_id = season_row['id']
            
            sql = f"""
            SELECT
              p.display_name,
              COUNT(DISTINCT ps.match_id) as matches_played,
              SUM(ps.{stat_col}) as total_{stat_col}
            FROM player_stats ps
            JOIN matches m ON m.id = ps.match_id
            JOIN divisions d ON d.id = m.division_id
            JOIN players p ON p.id = ps.player_id
            WHERE d.league_type = %s AND m.season_id = %s
            GROUP BY p.id, p.display_name
            ORDER BY total_{stat_col} DESC NULLS LAST
            LIMIT %s
            """
            
            cur.execute(sql, (league.upper(), str(season_id), limit))
            return [dict(row) for row in cur.fetchall()]


def get_player_averages(league: str, stat: str = 'goals', limit: int = 50, min_matches: int = 1, season_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get player averages per match (minimum matches threshold).
    
    Args:
        league: 'NBL', 'MBL', or 'NCBA'
        stat: 'goals', 'assists', 'saves', 'blocks', 'points'
        limit: Max players to return
        min_matches: Minimum matches played to appear in results
        season_id: Season UUID; if None, uses active season
    
    Returns:
        List of players with average stats per match
    """
    stat_col = stat.lower()
    if stat_col not in ['goals', 'own_goals', 'assists', 'saves', 'blocks', 'powerup_plays', 'points']:
        raise ValueError(f"Unknown stat: {stat}")
    
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not season_id:
                cur.execute('SELECT id FROM seasons WHERE active = true LIMIT 1')
                season_row = cur.fetchone()
                if not season_row:
                    return []
                season_id = season_row['id']
            
            sql = f"""
            WITH player_totals AS (
              SELECT
                p.id,
                p.display_name,
                COUNT(DISTINCT ps.match_id) as matches_played,
                SUM(ps.{stat_col}) as total_{stat_col}
              FROM player_stats ps
              JOIN matches m ON m.id = ps.match_id
              JOIN divisions d ON d.id = m.division_id
              JOIN players p ON p.id = ps.player_id
              WHERE d.league_type = %s AND m.season_id = %s
              GROUP BY p.id, p.display_name
            )
            SELECT
              display_name,
              matches_played,
              ROUND((total_{stat_col}::float / NULLIF(matches_played, 0))::numeric, 2) as avg_{stat_col}
            FROM player_totals
            WHERE matches_played >= %s
            ORDER BY avg_{stat_col} DESC NULLS LAST
            LIMIT %s
            """
            
            cur.execute(sql, (league.upper(), str(season_id), min_matches, limit))
            return [dict(row) for row in cur.fetchall()]


# ============================================================================
# MATCH RECORDING
# ============================================================================

def record_match(payload: Dict[str, Any]) -> str:
    """
    Record a match and its player stats.
    
    Expected payload keys:
    - season_id (uuid): Season reference
    - division_id (uuid): Division reference (can derive from league string)
    - league (str): 'NBL', 'MBL', or 'NCBA' (alternative to division_id)
    - team1_code (str): Home team code
    - team2_code (str): Away team code
    - team1_score (int): Home team score
    - team2_score (int): Away team score
    - final_turn (int, optional): Match length
    - recorded_by (int): Discord user ID
    - played_at (str, optional): Timestamp of match
    - player_stats (list): List of {player_name, goals, assists, ...} dicts
    
    Returns:
        Match UUID as string
    """
    # TODO: Implement validation, idempotency, and error handling
    logger.info(f"record_match not fully implemented; payload: {payload}")
    return "stub_match_id"


# ============================================================================
# UTILITY / ADMIN
# ============================================================================

def get_active_season() -> Optional[Dict[str, Any]]:
    """Get the currently active season."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM seasons WHERE active = true LIMIT 1')
            row = cur.fetchone()
            return dict(row) if row else None


def get_divisions() -> Dict[str, str]:
    """Get mapping of league names to division IDs."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT league_type, id FROM divisions')
            return {row[0]: row[1] for row in cur.fetchall()}


# ============================================================================
# SMOKE TEST
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        print("Testing adapter...")
        season = get_active_season()
        print(f"Active season: {season}")
        
        leaderboard = get_leaderboard(league='NBL', limit=5)
        print(f"NBL Leaderboard (first 5): {leaderboard}")
        
        print("✅ Adapter smoke test passed (if DB is running)")
    except Exception as e:
        logger.error(f"Smoke test failed: {e}")
        print(f"❌ Adapter not connected. Make sure DATABASE_URL is set and Supabase is running.")
