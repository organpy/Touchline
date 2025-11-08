"""Cache for teams and players with Discord autocomplete support."""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class TeamPlayerCache:
    """In-memory cache for teams and players, refreshed on bot startup and on changes."""
    
    _instance = None
    _teams: Dict[str, dict] = {}  # team_id -> team data
    _teams_by_code: Dict[str, dict] = {}  # team_code -> team data
    _players: Dict[str, dict] = {}  # player_id -> player data
    _players_by_discord: Dict[int, dict] = {}  # discord_id -> player data
    _players_by_name: Dict[str, dict] = {}  # display_name (lowercase) -> player data
    _team_players: Dict[str, List[dict]] = {}  # team_id -> [players in that team]
    _last_refresh: datetime = None
    _supabase = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def set_supabase(cls, supabase):
        """Set the supabase client (call this once at bot startup)."""
        cls._supabase = supabase

    @classmethod
    async def refresh(cls, supabase=None):
        """Refresh all caches from database."""
        if supabase:
            cls._supabase = supabase
        
        if not cls._supabase:
            logger.warning("⚠️ Supabase not set for TeamPlayerCache")
            return

        try:
            logger.info("🔄 Refreshing TeamPlayerCache...")
            
            # Load supporting metadata
            divisions_response = cls._supabase.table('divisions').select('id, league_type, name').execute()
            division_map = {d['id']: d for d in (divisions_response.data or [])}

            active_seasons_response = (
                cls._supabase
                .table('seasons')
                .select('id, name, division_id')
                .eq('active', True)
                .execute()
            )
            active_season_map = {s['division_id']: s for s in (active_seasons_response.data or []) if s.get('division_id')}

            # Load teams and enrich with league/season metadata
            teams_response = cls._supabase.table('teams').select('*').execute()
            cls._teams = {}
            cls._teams_by_code = {}
            for raw_team in (teams_response.data or []):
                team = dict(raw_team)
                division_id = team.get('division_id')
                division = division_map.get(division_id)
                if division:
                    team['division'] = division
                    team['league_type'] = division.get('league_type')
                    team['league_name'] = division.get('name')

                active_season = active_season_map.get(division_id)
                if active_season:
                    team['active_season_id'] = active_season.get('id')
                    team['active_season_name'] = active_season.get('name')

                team_id = team.get('id')
                if team_id:
                    cls._teams[team_id] = team

                code = team.get('code')
                if code:
                    cls._teams_by_code[code.upper()] = team
            
            # Load players
            players_response = cls._supabase.table('players').select('*').execute()
            cls._players = {p['id']: p for p in (players_response.data or [])}
            cls._players_by_discord = {p['discord_id']: p for p in (players_response.data or []) if p.get('discord_id')}
            cls._players_by_name = {p['display_name'].lower(): p for p in (players_response.data or [])}
            
            # Load active rosters (team -> players mapping)
            rosters_response = cls._supabase.table('rosters').select(
                '*, players(id, display_name, discord_id), teams(id, code)'
            ).is_('left_at', None).execute()
            
            cls._team_players = {}
            for roster in (rosters_response.data or []):
                team_id = roster.get('team_id')
                if team_id:
                    if team_id not in cls._team_players:
                        cls._team_players[team_id] = []
                    player_data = roster.get('players')
                    if player_data:
                        cls._team_players[team_id].append(player_data)
            
            cls._last_refresh = datetime.utcnow()
            total_teams = len(cls._teams)
            total_players = len(cls._players)
            logger.info(
                "✅ TeamPlayerCache refreshed: %d teams, %d players, %d team rosters",
                total_teams, total_players, len(cls._team_players)
            )
        except Exception as exc:
            logger.error("❌ Failed to refresh TeamPlayerCache: %s", exc)

    @classmethod
    def invalidate(cls):
        """Clear all caches."""
        cls._teams.clear()
        cls._teams_by_code.clear()
        cls._players.clear()
        cls._players_by_discord.clear()
        cls._players_by_name.clear()
        cls._team_players.clear()
        logger.info("🔄 TeamPlayerCache invalidated")

    # ==================== TEAMS ====================
    
    @classmethod
    def get_team(cls, team_id: str) -> Optional[dict]:
        """Get team by ID."""
        return cls._teams.get(team_id)

    @classmethod
    def get_team_by_code(cls, team_code: str) -> Optional[dict]:
        """Get team by code (case-insensitive)."""
        return cls._teams_by_code.get(team_code.upper())

    @classmethod
    def get_all_teams(cls) -> List[dict]:
        """Get all teams."""
        return list(cls._teams.values())

    @classmethod
    def get_team_codes(cls) -> List[str]:
        """Get all team codes for autocomplete."""
        return sorted(cls._teams_by_code.keys())

    # ==================== PLAYERS ====================
    
    @classmethod
    def get_player(cls, player_id: str) -> Optional[dict]:
        """Get player by ID."""
        return cls._players.get(player_id)

    @classmethod
    def get_player_by_discord(cls, discord_id: int) -> Optional[dict]:
        """Get player by Discord ID."""
        return cls._players_by_discord.get(discord_id)

    @classmethod
    def get_player_by_name(cls, display_name: str) -> Optional[dict]:
        """Get player by display name (case-insensitive)."""
        return cls._players_by_name.get(display_name.lower())

    @classmethod
    def get_all_players(cls) -> List[dict]:
        """Get all players."""
        return list(cls._players.values())

    @classmethod
    def get_player_names(cls) -> List[str]:
        """Get all player display names for autocomplete."""
        return sorted(cls._players_by_name.keys())

    # ==================== TEAM ROSTERS ====================
    
    @classmethod
    def get_team_players(cls, team_id: str) -> List[dict]:
        """Get active players on a team."""
        return cls._team_players.get(team_id, [])

    @classmethod
    def get_team_player_names(cls, team_id: str) -> List[str]:
        """Get display names of players on a team, for autocomplete."""
        players = cls.get_team_players(team_id)
        return sorted([p.get('display_name', '?') for p in players])

    # ==================== CACHE STATUS ====================
    
    @classmethod
    def is_fresh(cls) -> bool:
        """Check if cache was recently loaded."""
        if not cls._last_refresh:
            return False
        age = datetime.utcnow() - cls._last_refresh
        return age < timedelta(minutes=5)

    @classmethod
    def get_status(cls) -> dict:
        """Get cache status for debugging."""
        return {
            'teams': len(cls._teams),
            'players': len(cls._players),
            'team_rosters': len(cls._team_players),
            'last_refresh': cls._last_refresh.isoformat() if cls._last_refresh else None,
            'is_fresh': cls.is_fresh()
        }


def get_team_player_cache() -> TeamPlayerCache:
    """Get the singleton cache instance."""
    return TeamPlayerCache()
