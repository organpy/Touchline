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
        """Load all teams and active rosters from Supabase at startup"""
        logger.info("🔄 Loading roster cache...")
        try:
            # Get all teams across all divisions with division_id for league validation
            teams_response = await execute_async(supabase_client.table('teams').select('id, code, name, emoji, division_id'))
            for team_row in teams_response.data:
                team = CachedTeam(
                    team_id=team_row['id'],
                    code=team_row['code'],
                    name=team_row['name'],
                    emoji=team_row.get('emoji'),
                    division_id=team_row.get('division_id')
                )
                self.teams[team_row['code']] = team
                logger.debug(f"  Cached team: {team_row['code']}")

            # Get all active rosters (left_at IS NULL) - NO season filter
            # This includes players from ALL seasons who are currently active (left_at IS NULL)
            rosters_response = await execute_async(supabase_client.table('rosters').select(
                'id, player_id, team_id, season_id, joined_at, left_at, '
                'players(id, display_name, discord_id), teams(code)'
            ).is_('left_at', 'null'))

            roster_count = 0
            loaded_player_ids = set()  # Track unique players to avoid duplicates
            
            for roster_row in rosters_response.data:
                player_data = roster_row['players']
                team_code = roster_row['teams']['code']
                player_id = player_data['id']

                # Skip if we've already loaded this player (they might be in multiple rosters)
                if player_id in loaded_player_ids:
                    continue

                player = CachedPlayer(
                    player_id=player_id,
                    display_name=player_data['display_name'],
                    discord_id=player_data.get('discord_id')
                )

                # Cache player (once per unique player)
                self.players[player_id] = player
                loaded_player_ids.add(player_id)

                # Add to team's active players
                if team_code in self.teams:
                    # Avoid duplicates in team roster
                    if not any(p.player_id == player_id for p in self.teams[team_code].active_players):
                        self.teams[team_code].active_players.append(player)
                        roster_count += 1

            self.loaded = True
            logger.info(
                f"✅ Roster cache loaded: {len(self.teams)} teams, "
                f"{len(self.players)} active players ({roster_count} roster entries)"
            )

        except Exception as e:
            logger.error(f"❌ Failed to load roster cache: {e}", exc_info=True)
            self.loaded = False

    def get_team_by_code(self, code: str) -> Optional[CachedTeam]:
        """Get team by code"""
        return self.teams.get(code)

    def teams_in_same_league(self, team_code1: str, team_code2: str) -> bool:
        """Check if two teams are in the same league (division)"""
        team1 = self.teams.get(team_code1)
        team2 = self.teams.get(team_code2)
        
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
