"""Cache for teams and players with Discord autocomplete support."""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from utils.team_colors import get_team_color
from db.db_utils import execute_async

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
    async def invalidate_team(cls, team_id: str) -> None:
        """
        Invalidate a single team's cache entry to avoid full refresh on roster mutations.
        Use this after signing/releasing a player to a specific team.
        """
        if team_id not in cls._teams:
            return

        try:
            logger.info("♻️ Invalidating team cache for %s", team_id)

            # Reload just this team and its roster
            team_response = await execute_async(cls._supabase.table("teams").select("*").eq("id", team_id))
            if not team_response.data:
                logger.warning("Team %s not found in database", team_id)
                return

            raw_team = team_response.data[0]
            division_id = raw_team.get("division_id")

            # Reload division info
            division_response = (
                await execute_async(cls._supabase.table("divisions").select("id, league_type, name").eq("id", division_id))
            )
            division = division_response.data[0] if division_response.data else {}

            # Reload active season for this team's division
            season_response = await execute_async(
                cls._supabase
                .table("seasons")
                .select("id, name, division_id")
                .eq("division_id", division_id)
                .eq("active", True)
            )
            active_season = season_response.data[0] if season_response.data else {}

            # Enrich team with metadata
            team = dict(raw_team)
            team["division"] = division
            team["league_type"] = division.get("league_type")
            team["league_name"] = division.get("name")
            if active_season:
                team["active_season_id"] = active_season.get("id")
                team["active_season_name"] = active_season.get("name")

            # Update team in all caches
            cls._teams[team_id] = team
            code = team.get("code")
            if code:
                cls._teams_by_code[code.lower()] = team

            # Reload team roster
            roster_response = await execute_async(cls._supabase.table("rosters").select(
                "*, players(id, display_name, discord_id)"
            ).eq("team_id", team_id).is_("left_at", None))

            cls._team_players[team_id] = []
            for roster in roster_response.data or []:
                player_data = roster.get("players") or {}
                player_id = roster.get("player_id") or player_data.get("id")
                player = cls._players.get(player_id)
                if player and player not in cls._team_players[team_id]:
                    cls._team_players[team_id].append(player)

            logger.info("✅ Team cache invalidated for %s (%d players)", team_id, len(cls._team_players[team_id]))

        except Exception as exc:
            logger.error("Failed to invalidate team %s: %s", team_id, exc)

    @classmethod
    async def invalidate_season(cls, season_id: str) -> None:
        """
        Invalidate cache for all teams in a season.
        Use this after bulk roster operations or season changes.
        """
        if not season_id:
            return

        try:
            logger.info("♻️ Invalidating cache for season %s", season_id)

            # Get all teams in this season's division
            season_response = await execute_async(cls._supabase.table("seasons").select("division_id").eq("id", season_id))
            if not season_response.data:
                logger.warning("Season %s not found", season_id)
                return

            division_id = season_response.data[0].get("division_id")

            # Find all teams in this division
            teams_response = await execute_async(cls._supabase.table("teams").select("id").eq("division_id", division_id))

            for team_data in teams_response.data or []:
                await cls.invalidate_team(team_data["id"])

            logger.info("✅ Season cache invalidated for %s", season_id)

        except Exception as exc:
            logger.error("Failed to invalidate season %s: %s", season_id, exc)

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
            divisions_response = await execute_async(cls._supabase.table('divisions').select('id, league_type, name'))
            division_map = {d['id']: d for d in (divisions_response.data or [])}

            active_seasons_response = await execute_async(
                cls._supabase
                .table('seasons')
                .select('id, name, division_id')
                .eq('active', True)
            )
            active_season_map = {s['division_id']: s for s in (active_seasons_response.data or []) if s.get('division_id')}

            # Load teams and enrich with league/season metadata
            teams_response = await execute_async(cls._supabase.table('teams').select('*'))
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
                    # Attach color_primary from team_colors (may be None if not yet defined)
                    color_val = get_team_color(code)
                    if color_val is not None:
                        team['color_primary'] = color_val
                    cls._teams_by_code[code.lower()] = team
            
            # Load players
            players_response = await execute_async(cls._supabase.table('players').select('*'))
            cls._players = {}
            for raw_player in (players_response.data or []):
                player = dict(raw_player)
                if player.get('id'):
                    cls._players[player['id']] = player
            
            # Load active rosters (team -> players mapping)
            rosters_response = await execute_async(cls._supabase.table('rosters').select(
                '*, players(id, display_name, discord_id), teams(id, code)'
            ).is_('left_at', None))

            season_ids = {r.get('season_id') for r in (rosters_response.data or []) if r.get('season_id')}
            seasons_map: Dict[str, dict] = {}
            if season_ids:
                seasons_response = await execute_async(
                    cls._supabase
                    .table('seasons')
                    .select('id, name, division_id')
                    .in_('id', list(season_ids))
                )
                seasons_map = {s['id']: s for s in (seasons_response.data or [])}
            
            cls._team_players = {}
            for player in cls._players.values():
                player['active_memberships'] = []
                player['memberships_by_season'] = {}
                player.pop('current_team_id', None)
                player.pop('current_team_code', None)
                player.pop('current_team_name', None)
                player.pop('current_league_type', None)
                player.pop('current_league_name', None)
                player.pop('current_season_id', None)
                player.pop('current_season_name', None)

            for roster in (rosters_response.data or []):
                team_id = roster.get('team_id')
                player_data = roster.get('players') or {}
                player_id = roster.get('player_id') or player_data.get('id')
                season_id = roster.get('season_id')
                team_info = cls._teams.get(team_id) or roster.get('teams') or {}
                division_id = team_info.get('division_id')
                season = seasons_map.get(season_id)
                if not division_id:
                    division_id = (season or {}).get('division_id')
                division = division_map.get(division_id)
                season_name = (season or {}).get('name')
                if not season_name and division_id:
                    season_name = (active_season_map.get(division_id) or {}).get('name')
                league_type = team_info.get('league_type') or (division or {}).get('league_type')
                league_name = team_info.get('league_name') or (division or {}).get('name')

                if team_id:
                    if team_id not in cls._team_players:
                        cls._team_players[team_id] = []
                    player_entry = cls._players.get(player_id)
                    if player_entry and player_entry not in cls._team_players[team_id]:
                        cls._team_players[team_id].append(player_entry)

                player_entry = cls._players.get(player_id)
                if not player_entry:
                    continue

                membership = {
                    'team_id': team_id,
                    'team_code': team_info.get('code'),
                    'team_name': team_info.get('name'),
                    'season_id': season_id,
                    'season_name': season_name,
                    'division_id': division_id,
                    'league_type': league_type,
                    'league_name': league_name,
                }

                player_entry['active_memberships'].append(membership)
                if season_id:
                    player_entry['memberships_by_season'][season_id] = membership

                if not player_entry.get('current_team_id'):
                    player_entry['current_team_id'] = membership.get('team_id')
                    player_entry['current_team_code'] = membership.get('team_code')
                    player_entry['current_team_name'] = membership.get('team_name')
                    player_entry['current_league_type'] = membership.get('league_type')
                    player_entry['current_league_name'] = membership.get('league_name')
                    player_entry['current_season_id'] = membership.get('season_id')
                    player_entry['current_season_name'] = membership.get('season_name')

            cls._players_by_discord = {}
            cls._players_by_name = {}
            for player in cls._players.values():
                discord_id = player.get('discord_id')
                if discord_id:
                    cls._players_by_discord[discord_id] = player
                display_name = player.get('display_name')
                if display_name:
                    cls._players_by_name[display_name.lower()] = player
            
            cls._last_refresh = datetime.now(timezone.utc)
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
        cls._last_refresh = None
        logger.info("🔄 TeamPlayerCache invalidated")

    # ==================== TEAMS ====================
    
    @classmethod
    def get_team(cls, team_id: str) -> Optional[dict]:
        """Get team by ID."""
        return cls._teams.get(team_id)

    @classmethod
    def get_team_by_code(cls, team_code: str) -> Optional[dict]:
        """Get team by code (case-insensitive)."""
        return cls._teams_by_code.get(team_code.lower())

    @classmethod
    def get_all_teams(cls) -> List[dict]:
        """Get all teams."""
        return list(cls._teams.values())

    @classmethod
    def get_team_by_owner(cls, owner_discord_id: int) -> Optional[dict]:
        """Get team by owner Discord ID."""
        for team in cls._teams.values():
            if team.get('owner_discord_id') == owner_discord_id:
                return team
        return None

    @classmethod
    def get_active_season_for_division(cls, division_id: str) -> Optional[dict]:
        """Get active season for a division from enriched team data."""
        for team in cls._teams.values():
            if team.get('division_id') == division_id and team.get('active_season_id'):
                return {
                    'id': team['active_season_id'],
                    'name': team.get('active_season_name'),
                    'division_id': division_id,
                }
        return None

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

    @classmethod
    def get_team_by_player_id(cls, player_id: str) -> Optional[dict]:
        """Get the team that a player is currently on, if any."""
        for team_id, players in cls._team_players.items():
            for player in players:
                if player.get('id') == player_id:
                    return cls._teams.get(team_id)
        return None

    @classmethod
    def get_player_by_id(cls, player_id: str) -> Optional[dict]:
        """Alias for get_player - get player by ID."""
        return cls._players.get(player_id)

    # ==================== CACHE STATUS ====================
    
    @classmethod
    def is_fresh(cls) -> bool:
        """Check if cache was recently loaded."""
        if not cls._last_refresh:
            return False
        age = datetime.now(timezone.utc) - cls._last_refresh
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
