"""
Roster cache - loaded at bot startup for instant lookups
Singleton pattern to ensure only one cache in memory
Imported independently to avoid circular imports
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
import logging
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


@dataclass
class CachedPlayer:
    """Cached player info"""
    player_id: str
    display_name: str
    discord_id: Optional[int] = None


@dataclass
class CachedTeam:
    """Cached team info"""
    team_id: str
    code: str
    name: str
    emoji: Optional[str] = None
    division_id: Optional[str] = None
    active_players: List[CachedPlayer] = None

    def __post_init__(self):
        if self.active_players is None:
            self.active_players = []


class RosterCache:
    """Singleton cache of teams & players loaded at bot startup"""
    _instance: Optional['RosterCache'] = None
    teams: Dict[str, CachedTeam]
    players: Dict[str, CachedPlayer]
    loaded: bool

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.teams = {}
            cls._instance.players = {}
            cls._instance.loaded = False
        return cls._instance

    async def load_cache(self, supabase_client):
        """Load active teams and the active season's rosters (a player is on at most one team)."""
        logger.info("🔄 Loading roster cache...")
        try:
            self.teams = {}
            self.players = {}
            season_response = await execute_async(
                supabase_client.table('seasons').select('id').eq('active', True).limit(1)
            )
            season_id = season_response.data[0]['id'] if season_response.data else None

            divisions: Dict[str, Optional[str]] = {}
            if season_id:
                assignments = await execute_async(
                    supabase_client.table('season_teams').select('team_id, division_id').eq('season_id', season_id)
                )
                divisions = {row['team_id']: row['division_id'] for row in (assignments.data or [])}

            teams_response = await execute_async(
                supabase_client.table('teams').select('id, code, name, emoji').is_('archived_at', 'null')
            )
            for team_row in teams_response.data or []:
                self.teams[team_row['code']] = CachedTeam(
                    team_id=team_row['id'],
                    code=team_row['code'],
                    name=team_row['name'],
                    emoji=team_row.get('emoji'),
                    division_id=divisions.get(team_row['id']),
                )

            roster_count = 0
            if season_id:
                rosters_response = await execute_async(supabase_client.table('rosters').select(
                    'player_id, team_id, players(id, display_name, discord_id), teams(code)'
                ).eq('season_id', season_id).is_('left_at', 'null'))
                for roster_row in rosters_response.data or []:
                    player_data = roster_row.get('players') or {}
                    team_code = (roster_row.get('teams') or {}).get('code')
                    if not player_data or team_code not in self.teams:
                        continue
                    player = CachedPlayer(
                        player_id=player_data['id'],
                        display_name=player_data['display_name'],
                        discord_id=player_data.get('discord_id'),
                    )
                    self.players[player.player_id] = player
                    self.teams[team_code].active_players.append(player)
                    roster_count += 1

            self.loaded = True
            logger.info(
                f"✅ Roster cache loaded: {len(self.teams)} teams, {roster_count} active players"
            )

        except Exception as e:
            logger.error(f"❌ Failed to load roster cache: {e}", exc_info=True)
            self.loaded = False

    def get_team_by_code(self, code: str) -> Optional[CachedTeam]:
        """Get team by code (codes are lowercase slugs)"""
        if not code:
            return None
        return self.teams.get(code) or self.teams.get(code.strip().lower())

    def teams_in_same_league(self, team_code1: str, team_code2: str) -> bool:
        """Check if two teams are in the same league (division)"""
        team1 = self.get_team_by_code(team_code1)
        team2 = self.get_team_by_code(team_code2)
        
        if not team1 or not team2:
            return False
        
        if not team1.division_id or not team2.division_id:
            return False
        
        return team1.division_id == team2.division_id

    def get_all_teams(self) -> List[CachedTeam]:
        """Get all cached teams"""
        return list(self.teams.values())

    def get_team_players(self, team_code: str) -> List[CachedPlayer]:
        """Get active players for a team"""
        team = self.teams.get(team_code)
        return team.active_players if team else []

    def get_player_by_id(self, player_id: str) -> Optional[CachedPlayer]:
        """Get player by ID"""
        return self.players.get(player_id)

    def search_players(self, team_code: str, query: str) -> List[CachedPlayer]:
        """Search players on a team by name (for autocomplete)"""
        team = self.teams.get(team_code)
        if not team:
            return []

        query_lower = query.lower()
        return [p for p in team.active_players if query_lower in p.display_name.lower()]

    @classmethod
    def reset(cls):
        """Reset cache (for testing)"""
        cls._instance = None

    def invalidate(self):
        """Clear cache - NOTE: Call reload() after to repopulate"""
        self.teams.clear()
        self.players.clear()
        self.loaded = False
        logger.info("🔄 RosterCache invalidated (loaded=False)")

    async def reload(self, supabase_client):
        """Invalidate and reload cache from database"""
        self.invalidate()
        await self.load_cache(supabase_client)
        logger.info("✅ RosterCache reloaded")
