"""
Roster cache - loaded at bot startup for instant lookups
Singleton pattern to ensure only one cache in memory
Imported independently to avoid circular imports
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
import logging

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
            # Get all teams (assuming NBL division)
            teams_response = supabase_client.table('teams').select('*').execute()
            for team_row in teams_response.data:
                team = CachedTeam(
                    team_id=team_row['id'],
                    code=team_row['code'],
                    name=team_row['name'],
                    emoji=team_row.get('emoji')
                )
                self.teams[team_row['code']] = team
                logger.debug(f"  Cached team: {team_row['code']}")

            # Get active season
            seasons_response = supabase_client.table('seasons').select('id').eq('active', True).limit(1).execute()
            if not seasons_response.data:
                logger.warning("⚠️  No active season found")
                self.loaded = True
                return

            active_season_id = seasons_response.data[0]['id']
            logger.debug(f"  Active season: {active_season_id}")

            # Get all active rosters (left_at IS NULL)
            rosters_response = supabase_client.table('rosters').select(
                'id, player_id, team_id, season_id, joined_at, left_at, '
                'players(id, display_name, discord_id), teams(code)'
            ).eq('season_id', active_season_id).is_('left_at', 'null').execute()

            roster_count = 0
            for roster_row in rosters_response.data:
                player_data = roster_row['players']
                team_code = roster_row['teams']['code']

                player = CachedPlayer(
                    player_id=player_data['id'],
                    display_name=player_data['display_name'],
                    discord_id=player_data.get('discord_id')
                )

                # Cache player
                self.players[player_data['id']] = player

                # Add to team's active players
                if team_code in self.teams:
                    # Avoid duplicates
                    if not any(p.player_id == player.player_id for p in self.teams[team_code].active_players):
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
        """Clear cache"""
        self.teams.clear()
        self.players.clear()
        self.loaded = False
