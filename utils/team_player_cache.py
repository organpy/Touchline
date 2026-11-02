"""Cache for teams and players with Discord autocomplete support.

Teams are enriched with their division in the active season (``division_id``, ``division``,
``league_type`` = the division code, ``league_name``, ``group_id``) and the active season itself
(``active_season_id``/``active_season_name``) when they take part in it. Rosters are the active
season's: a player is on at most one team.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from db.db_utils import execute_async, fetch_all
from utils.league_cache import LeagueCache, enrich_team

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

    # ------------------------------------------------------------------ enrichment
    @classmethod
    def _enrich_team(cls, raw_team: dict) -> dict:
        return enrich_team(raw_team)

    @classmethod
    def _store_team(cls, team: dict) -> None:
        team_id = team.get("id")
        if team_id:
            cls._teams[team_id] = team
        code = team.get("code")
        if code:
            cls._teams_by_code[code.lower()] = team

    @staticmethod
    def _membership(team: dict, season: dict) -> dict:
        return {
            "team_id": team.get("id"),
            "team_code": team.get("code"),
            "team_name": team.get("name"),
            "season_id": season.get("id"),
            "season_name": season.get("name"),
            "division_id": team.get("division_id"),
            "league_type": team.get("league_type"),
            "league_name": team.get("league_name"),
        }

    @classmethod
    def _clear_membership(cls, player: dict) -> None:
        player["membership"] = None
        for key in ("current_team_id", "current_team_code", "current_team_name", "current_league_type",
                    "current_league_name", "current_season_id", "current_season_name"):
            player.pop(key, None)

    @classmethod
    def _attach_membership(cls, player: dict, team: dict, season: dict) -> None:
        membership = cls._membership(team, season)
        player["membership"] = membership  # a player is on at most one team per season
        player["current_team_id"] = membership["team_id"]
        player["current_team_code"] = membership["team_code"]
        player["current_team_name"] = membership["team_name"]
        player["current_league_type"] = membership["league_type"]
        player["current_league_name"] = membership["league_name"]
        player["current_season_id"] = membership["season_id"]
        player["current_season_name"] = membership["season_name"]

    # ------------------------------------------------------------------ refresh
    @classmethod
    async def invalidate_team(cls, team_id: str) -> None:
        """Reload one team (its row, division and active roster) after a roster or team change."""
        if not cls._supabase or not team_id:
            return
        try:
            team_response = await execute_async(cls._supabase.table("teams").select("*").eq("id", team_id))
            if not team_response.data:
                cls._teams.pop(team_id, None)
                return
            active = LeagueCache.active_season()
            if active:
                LeagueCache._assignments.pop(active["id"], None)
                await LeagueCache.load_season_teams(active["id"])
            old = cls._teams.get(team_id)
            if old and old.get("code"):
                cls._teams_by_code.pop(old["code"].lower(), None)
            team = cls._enrich_team(team_response.data[0])
            cls._store_team(team)

            previous = cls._team_players.get(team_id, [])
            for player in previous:
                cls._clear_membership(player)
            cls._team_players[team_id] = []
            if active:
                roster_response = await execute_async(
                    cls._supabase.table("rosters")
                    .select("player_id")
                    .eq("team_id", team_id)
                    .eq("season_id", active["id"])
                    .is_("left_at", "null")
                )
                for roster in roster_response.data or []:
                    player = cls._players.get(roster.get("player_id"))
                    if player is None:
                        await cls._load_player(roster.get("player_id"))
                        player = cls._players.get(roster.get("player_id"))
                    if player:
                        for other_team, players in cls._team_players.items():
                            if other_team != team_id and player in players:
                                players.remove(player)
                        cls._attach_membership(player, team, active)
                        cls._team_players[team_id].append(player)
            logger.info("✅ Team cache invalidated for %s (%d players)", team_id, len(cls._team_players[team_id]))
        except Exception as exc:
            logger.error("Failed to invalidate team %s: %s", team_id, exc)

    @classmethod
    async def _load_player(cls, player_id: Optional[str]) -> None:
        if not player_id:
            return
        response = await execute_async(cls._supabase.table("players").select("*").eq("id", player_id))
        for raw in response.data or []:
            player = dict(raw)
            cls._clear_membership(player)
            cls._players[player["id"]] = player
            if player.get("discord_id"):
                cls._players_by_discord[player["discord_id"]] = player
            if player.get("display_name"):
                cls._players_by_name[player["display_name"].lower()] = player

    @classmethod
    async def invalidate_season(cls, season_id: str) -> None:
        """Season-wide changes (bulk roster edits, rollover) need a full refresh."""
        await cls.refresh()

    @classmethod
    async def refresh(cls, supabase=None):
        """Refresh all caches from the database."""
        if supabase:
            cls._supabase = supabase
        if not cls._supabase:
            logger.warning("⚠️ Supabase not set for TeamPlayerCache")
            return

        try:
            logger.info("🔄 Refreshing TeamPlayerCache...")
            await LeagueCache.refresh(cls._supabase)
            active = LeagueCache.active_season()

            # Fetch everything first (paged: Supabase returns at most 1000 rows per request), then rebuild the
            # in-memory state without awaiting, so readers never see a half-built cache.
            supabase = cls._supabase
            raw_teams = await fetch_all(lambda: supabase.table("teams").select("*").order("id"))
            raw_players = await fetch_all(lambda: supabase.table("players").select("*").order("id"))
            raw_rosters = []
            if active:
                raw_rosters = await fetch_all(
                    lambda: supabase.table("rosters")
                    .select("player_id, team_id")
                    .eq("season_id", active["id"])
                    .is_("left_at", "null")
                    .order("id")
                )

            cls._teams = {}
            cls._teams_by_code = {}
            for raw_team in raw_teams:
                cls._store_team(cls._enrich_team(raw_team))

            cls._players = {}
            for raw_player in raw_players:
                player = dict(raw_player)
                cls._clear_membership(player)
                if player.get("id"):
                    cls._players[player["id"]] = player

            cls._team_players = {}
            if active:
                for roster in raw_rosters:
                    team = cls._teams.get(roster.get("team_id"))
                    player = cls._players.get(roster.get("player_id"))
                    if not team or not player:
                        continue
                    cls._team_players.setdefault(team["id"], []).append(player)
                    cls._attach_membership(player, team, active)

            cls._players_by_discord = {}
            cls._players_by_name = {}
            for player in cls._players.values():
                if player.get("discord_id"):
                    cls._players_by_discord[player["discord_id"]] = player
                if player.get("display_name"):
                    cls._players_by_name[player["display_name"].lower()] = player

            cls._last_refresh = datetime.now(timezone.utc)
            logger.info(
                "✅ TeamPlayerCache refreshed: %d teams, %d players, %d team rosters",
                len(cls._teams), len(cls._players), len(cls._team_players),
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
    def is_loaded(cls) -> bool:
        """True once teams have been loaded, so a missing team really is missing (not just not loaded yet)."""
        return bool(cls._teams)

    @classmethod
    def get_team(cls, team_id: str) -> Optional[dict]:
        return cls._teams.get(team_id)

    @classmethod
    def get_team_by_code(cls, team_code: str) -> Optional[dict]:
        return cls._teams_by_code.get((team_code or "").lower())

    @classmethod
    def find_team(cls, identifier: Optional[str]) -> Optional[dict]:
        """Team by code, exact name or dashed name (case-insensitive)."""
        if not identifier:
            return None
        text = identifier.strip().lower()
        team = cls._teams_by_code.get(text) or cls._teams_by_code.get(text.replace(" ", "-"))
        if team:
            return team
        for candidate in cls._teams.values():
            if (candidate.get("name") or "").lower() == text:
                return candidate
        return None

    @classmethod
    def get_all_teams(cls, include_archived: bool = False) -> List[dict]:
        return [t for t in cls._teams.values() if include_archived or not t.get("archived_at")]

    @classmethod
    def get_season_teams(cls, division_id: Optional[str] = None) -> List[dict]:
        """Teams taking part in the active season, optionally in one division, by name."""
        teams = [t for t in cls._teams.values() if t.get("active_season_id")]
        if division_id:
            teams = [t for t in teams if t.get("division_id") == division_id]
        return sorted(teams, key=lambda t: (t.get("name") or "").lower())

    @classmethod
    def get_team_by_owner(cls, owner_discord_id: int) -> Optional[dict]:
        """The team a user owns (one team per owner)."""
        for team in cls._teams.values():
            if team.get("owner_discord_id") == owner_discord_id and not team.get("archived_at"):
                return team
        return None

    @classmethod
    def get_active_season_for_division(cls, division_id: str) -> Optional[dict]:
        """The active season, for code that still asks per division (seasons are league-wide now)."""
        active = LeagueCache.active_season()
        if not active:
            return None
        return {"id": active["id"], "name": active.get("name"), "division_id": division_id}

    @classmethod
    def get_team_codes(cls) -> List[str]:
        return sorted(code for code, team in cls._teams_by_code.items() if not team.get("archived_at"))

    # ==================== PLAYERS ====================

    @classmethod
    def get_player(cls, player_id: str) -> Optional[dict]:
        return cls._players.get(player_id)

    @classmethod
    def get_player_by_discord(cls, discord_id: int) -> Optional[dict]:
        return cls._players_by_discord.get(discord_id)

    @classmethod
    def get_player_by_name(cls, display_name: str) -> Optional[dict]:
        return cls._players_by_name.get((display_name or "").lower())

    @classmethod
    def get_all_players(cls) -> List[dict]:
        return list(cls._players.values())

    @classmethod
    def get_player_names(cls) -> List[str]:
        return sorted(cls._players_by_name.keys())

    # ==================== TEAM ROSTERS ====================

    @classmethod
    def get_team_players(cls, team_id: str) -> List[dict]:
        return cls._team_players.get(team_id, [])

    @classmethod
    def get_team_player_names(cls, team_id: str) -> List[str]:
        return sorted(p.get("display_name", "?") for p in cls.get_team_players(team_id))

    @classmethod
    def get_team_by_player_id(cls, player_id: str) -> Optional[dict]:
        player = cls._players.get(player_id)
        if player and player.get("current_team_id"):
            return cls._teams.get(player["current_team_id"])
        return None

    @classmethod
    def get_player_by_id(cls, player_id: str) -> Optional[dict]:
        return cls._players.get(player_id)

    # ==================== CACHE STATUS ====================

    @classmethod
    def is_fresh(cls) -> bool:
        if not cls._last_refresh:
            return False
        return datetime.now(timezone.utc) - cls._last_refresh < timedelta(minutes=5)

    @classmethod
    def get_status(cls) -> dict:
        return {
            "teams": len(cls._teams),
            "players": len(cls._players),
            "team_rosters": len(cls._team_players),
            "last_refresh": cls._last_refresh.isoformat() if cls._last_refresh else None,
            "is_fresh": cls.is_fresh(),
        }


def get_team_player_cache() -> TeamPlayerCache:
    """Get the singleton cache instance."""
    return TeamPlayerCache()
