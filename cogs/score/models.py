"""
Data models for stat registration
Dataclasses for type safety and serialization
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime, timezone


@dataclass
class PlayerStatEntry:
    """Per-player stat entry for a match"""
    player_id: str
    player_name: str
    goals: int = 0
    own_goals: int = 0
    points: int = 0
    assists: int = 0
    saves: int = 0
    blocks: int = 0
    powerup_plays: int = 0
    powerups_used: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for Supabase insert"""
        return {
            'player_id': self.player_id,
            'goals': self.goals,
            'own_goals': self.own_goals,
            'points': self.points,
            'assists': self.assists,
            'saves': self.saves,
            'blocks': self.blocks,
            'powerup_plays': self.powerup_plays,
            'powerups_used': self.powerups_used,
            # powerup_percentage computed by trigger
        }

    def __str__(self) -> str:
        """Display summary for UI"""
        return (
            f"{self.player_name}: {self.goals}G, {self.own_goals}OG, "
            f"{self.points}P, {self.assists}A, {self.saves}S, {self.blocks}B, "
            f"{self.powerup_plays}PP, {self.powerups_used}PU"
        )


@dataclass
class TeamMatchEntry:
    """Team's stats for a single match"""
    team_id: str
    team_code: str
    team_name: str
    emoji: Optional[str] = None
    score: int = 0
    players: Dict[str, PlayerStatEntry] = field(default_factory=dict)  # player_id -> PlayerStatEntry

    def add_player_stats(self, player_stat: PlayerStatEntry):
        """Add a player's stats"""
        self.players[player_stat.player_id] = player_stat

    def remove_player(self, player_id: str):
        """Remove a player's stats"""
        self.players.pop(player_id, None)

    def get_players_list(self):
        """Get list of PlayerStatEntry objects"""
        return list(self.players.values())

    def has_player(self, player_id: str) -> bool:
        """Check if player already added"""
        return player_id in self.players

    def __str__(self) -> str:
        """Display summary for confirmation"""
        player_summaries = "\n".join(f"  • {str(p)}" for p in self.players.values())
        return f"**{self.team_name}** ({self.score} pts)\n{player_summaries if player_summaries else '  (no players)'}"


@dataclass
class MatchEntry:
    """Complete match entry (team1, team2, all player stats)"""
    team1: TeamMatchEntry
    team2: TeamMatchEntry
    week: int = 1
    division_id: Optional[str] = None

    def to_supabase_match(self, season_id: str, division_id: str) -> dict:
        """Convert to match row for Supabase insert"""
        # Determine winner
        if self.team1.score > self.team2.score:
            team1_result = 'WIN'
            team2_result = 'LOSS'
        else:
            team1_result = 'LOSS'
            team2_result = 'WIN'

        return {
            'season_id': season_id,
            'division_id': division_id,
            'team1_id': self.team1.team_id,
            'team2_id': self.team2.team_id,
            'team1_score': self.team1.score,
            'team2_score': self.team2.score,
            'status': 'final',
            'played_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'team1_result': team1_result,
                'team2_result': team2_result
            }
        }

    def to_supabase_player_stats(self, match_id: str) -> list:
        """Convert to player_stats rows for Supabase insert"""
        stats = []
        for player_stat in self.team1.get_players_list():
            stat_dict = player_stat.to_dict()
            stat_dict['match_id'] = match_id
            stats.append(stat_dict)

        for player_stat in self.team2.get_players_list():
            stat_dict = player_stat.to_dict()
            stat_dict['match_id'] = match_id
            stats.append(stat_dict)

        return stats

    def get_total_player_count(self) -> int:
        """Total players across both teams"""
        return len(self.team1.players) + len(self.team2.players)
