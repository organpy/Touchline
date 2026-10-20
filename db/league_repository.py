"""Data access for the league structure: divisions, groups, seasons, division assignments,
standings, brackets and series.

The database enforces the league rules (see db/migrations/init.sql) and reports a violation with a
stable code in the error HINT. Those are raised here as ``LeagueRuleError`` with a message that can
be shown to the admin as-is.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from postgrest.exceptions import APIError

from db.db_utils import execute_async

logger = logging.getLogger(__name__)

RULE_MESSAGES: Dict[str, str] = {
    "MATCH_NOT_REGULAR_PHASE": "Regular-season games can only be reported while the regular season is running.",
    "MATCH_NOT_POSTSEASON": "Playoff games can only be reported during the postseason.",
    "MATCH_TEAM_NOT_IN_SEASON": "Both teams need a division this season before they can play.",
    "MATCH_DIVISION_MISMATCH": "Both teams must be in the same division this season.",
    "MATCH_SERIES_SEASON": "That playoff series belongs to another season.",
    "MATCH_SERIES_NOT_READY": "That playoff series is not open for games (it is waiting for teams or already decided).",
    "MATCH_SERIES_TEAMS": "Those two teams are not playing each other in that series.",
    "MATCH_LOCKED": "That result is locked because its part of the season has finished.",
    "MATCH_IMMUTABLE": "A game's teams cannot change. Void it and report it again.",
    "MATCH_INVALID": "New games must be reported as final.",
    "MATCH_NO_SERIES": "These teams don't have an open playoff series right now.",
    "MATCH_WRONG_PHASE": "Games can only be reported during the regular season or the playoffs.",
    "MATCH_LIMIT": "Every game between these teams has already been recorded.",
    "SERIES_DOWNSTREAM_PLAYED": "A later playoff series already has results. Void those games first.",
    "SEASON_BAD_TRANSITION": "The season cannot move to that phase from where it is now.",
    "SEASON_WRONG_PHASE": "The season is not in the right phase for that.",
    "SEASON_TEAMS_LOCKED": "Division assignments are locked once the regular season has ended.",
    "SEASON_TEAM_HAS_MATCHES": "That team already has games this season, so its division cannot change.",
    "GROUP_DIVISION_MISMATCH": "That group belongs to a different division.",
    "DIVISION_ARCHIVED": "That division is archived.",
    "ROSTER_TEAM_NOT_IN_SEASON": "That team has no division this season yet. Assign it in the dashboard first.",
    "ROSTER_PLAYER_ON_TEAM": "That player is already on another team this season. Release or transfer them first.",
    "BRACKET_STARTED": "That bracket already has results, so seeds can no longer change.",
    "BRACKET_TEAM_MISSING": "Both teams must be in that bracket.",
    "BRACKETS_IN_PROGRESS": "Some playoff brackets are still being played.",
    "PLAN_INVALID": "The playoff plan is invalid.",
    "DUPLICATE": "That already exists.",
}

_UNIQUE_HINTS = (
    (re.compile(r"ux_divisions_code"), "A division with that code already exists."),
    (re.compile(r"ex_divisions_tier"), "Another division already has that tier."),
    (re.compile(r"ux_teams_code"), "A team with that code already exists."),
    (re.compile(r"ux_teams_owner"), "That user already owns a team."),
    (re.compile(r"ux_division_groups_name"), "That division already has a group with that name."),
    (re.compile(r"ux_seasons_name"), "A season with that name already exists."),
    (re.compile(r"ux_rosters_one_active_team"), "That player is already on a team this season."),
)


class LeagueRuleError(Exception):
    """A league rule refused the change. ``message`` is safe to show to users."""

    def __init__(self, code: str, message: Optional[str] = None, detail: Optional[str] = None):
        self.code = code
        self.message = message or RULE_MESSAGES.get(code) or "That change is not allowed."
        self.detail = detail
        super().__init__(f"{code}: {self.message}")


def rule_error_from_api_error(exc: APIError) -> Optional[LeagueRuleError]:
    """Translate a PostgREST error into a LeagueRuleError when it is a known rule violation."""
    hint = getattr(exc, "hint", None)
    message = getattr(exc, "message", None) or str(exc)
    if hint and hint in RULE_MESSAGES:
        return LeagueRuleError(hint, detail=message)
    if getattr(exc, "code", None) in ("23505", "23P01"):  # unique / exclusion violation
        text = f"{message} {getattr(exc, 'details', '') or ''}"
        for pattern, friendly in _UNIQUE_HINTS:
            if pattern.search(text):
                return LeagueRuleError("DUPLICATE", friendly, detail=message)
        return LeagueRuleError("DUPLICATE", detail=message)
    if getattr(exc, "code", None) == "23514":  # check violation
        return LeagueRuleError("INVALID", "One of the values is not allowed (check codes, names and colours).", detail=message)
    return None


def friendly_error(exc: BaseException, fallback: str) -> str:
    """A message safe to show users: the rule message for known violations, else ``fallback``."""
    if isinstance(exc, LeagueRuleError):
        return exc.message
    if isinstance(exc, APIError):
        rule_error = rule_error_from_api_error(exc)
        if rule_error:
            return rule_error.message
    return fallback


async def run(query: Any) -> Any:
    """Execute a query, raising LeagueRuleError for rule violations."""
    try:
        return await execute_async(query)
    except APIError as exc:
        rule_error = rule_error_from_api_error(exc)
        if rule_error:
            raise rule_error from exc
        raise


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_team_code(raw: str) -> str:
    """Lowercase slug: letters, digits and dashes (spaces and underscores become dashes)."""
    code = re.sub(r"[\s_]+", "-", (raw or "").strip().lower())
    code = re.sub(r"[^a-z0-9-]", "", code)
    return re.sub(r"-{2,}", "-", code).strip("-")[:32]


def normalize_division_code(raw: str) -> str:
    code = re.sub(r"[^A-Za-z0-9_-]", "", (raw or "").strip())
    return code[:16].upper()


SEASON_PHASE_LABELS = {
    "setup": "Setup",
    "regular": "Regular season",
    "postseason": "Postseason",
    "offseason": "Offseason",
    "completed": "Completed",
}


class LeagueRepository:
    """Async data access for the league structure."""

    def __init__(self, supabase):
        self.supabase = supabase

    # ------------------------------------------------------------------ divisions
    async def list_divisions(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        query = self.supabase.table("divisions").select("*").order("tier")
        if not include_archived:
            query = query.is_("archived_at", "null")
        response = await run(query)
        return response.data or []

    async def get_division(self, division_id: str) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("divisions").select("*").eq("id", division_id).limit(1))
        return (response.data or [None])[0]

    async def get_division_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        response = await run(
            self.supabase.table("divisions").select("*").eq("code", normalize_division_code(code)).limit(1)
        )
        return (response.data or [None])[0]

    async def create_division(
        self,
        code: str,
        name: str,
        tier: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        color: Optional[str] = None,
        emoji: Optional[str] = None,
    ) -> Dict[str, Any]:
        if tier is None:
            divisions = await self.list_divisions()
            tier = (max((d["tier"] for d in divisions), default=0)) + 1
        payload = {
            "code": normalize_division_code(code),
            "name": name.strip(),
            "tier": tier,
            "settings": settings or {},
            "color": color,
            "emoji": emoji,
        }
        response = await run(self.supabase.table("divisions").insert(payload))
        return response.data[0]

    async def update_division(self, division_id: str, **fields: Any) -> Dict[str, Any]:
        if "code" in fields and fields["code"] is not None:
            fields["code"] = normalize_division_code(fields["code"])
        fields["updated_at"] = utc_now_iso()
        response = await run(self.supabase.table("divisions").update(fields).eq("id", division_id))
        return (response.data or [{}])[0]

    async def update_division_settings(self, division_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        return await self.update_division(division_id, settings=settings)

    async def reorder_divisions(self, division_ids: List[str]) -> None:
        await run(self.supabase.rpc("reorder_divisions", {"p_division_ids": division_ids}))

    async def archive_division(self, division_id: str, season_id: Optional[str]) -> None:
        """Archive a division that has no teams in the active season, then close the tier gap."""
        if season_id:
            teams = await self.list_season_teams(season_id, division_id=division_id)
            if teams:
                raise LeagueRuleError(
                    "DIVISION_HAS_TEAMS", "Move this division's teams elsewhere before archiving it."
                )
        await run(
            self.supabase.table("divisions").update({"archived_at": utc_now_iso(), "updated_at": utc_now_iso()}).eq("id", division_id)
        )
        remaining = [d["id"] for d in await self.list_divisions()]
        if remaining:
            await self.reorder_divisions(remaining)

    # ------------------------------------------------------------------ groups
    async def list_groups(self, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = self.supabase.table("division_groups").select("*").order("sort_order").order("name")
        if division_id:
            query = query.eq("division_id", division_id)
        response = await run(query)
        return response.data or []

    async def create_group(self, division_id: str, name: str) -> Dict[str, Any]:
        existing = await self.list_groups(division_id)
        payload = {"division_id": division_id, "name": name.strip(), "sort_order": len(existing)}
        response = await run(self.supabase.table("division_groups").insert(payload))
        return response.data[0]

    async def rename_group(self, group_id: str, name: str) -> None:
        await run(self.supabase.table("division_groups").update({"name": name.strip()}).eq("id", group_id))

    async def delete_group(self, group_id: str) -> None:
        await run(self.supabase.table("division_groups").delete().eq("id", group_id))

    # ------------------------------------------------------------------ seasons
    async def get_active_season(self) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("seasons").select("*").eq("active", True).limit(1))
        return (response.data or [None])[0]

    async def get_season(self, season_id: str) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("seasons").select("*").eq("id", season_id).limit(1))
        return (response.data or [None])[0]

    async def list_seasons(self) -> List[Dict[str, Any]]:
        response = await run(self.supabase.table("seasons").select("*").order("number", desc=True))
        return response.data or []

    async def create_first_season(self, name: str) -> Dict[str, Any]:
        """Create the league's first season (later ones come from start_next_season)."""
        if await self.list_seasons():
            raise LeagueRuleError("SEASON_WRONG_PHASE", "A season already exists. Start the next one from the dashboard.")
        response = await run(
            self.supabase.table("seasons").insert({"number": 1, "name": name.strip(), "active": True})
        )
        return response.data[0]

    async def set_season_phase(self, season_id: str, phase: str) -> None:
        await run(self.supabase.table("seasons").update({"phase": phase}).eq("id", season_id))

    async def rename_season(self, season_id: str, name: str) -> None:
        await run(self.supabase.table("seasons").update({"name": name.strip()}).eq("id", season_id))

    # ------------------------------------------------------------------ teams
    async def list_teams(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        query = self.supabase.table("teams").select("*").order("name")
        if not include_archived:
            query = query.is_("archived_at", "null")
        response = await run(query)
        return response.data or []

    async def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("teams").select("*").eq("id", team_id).limit(1))
        return (response.data or [None])[0]

    async def create_team(
        self,
        code: str,
        name: str,
        emoji: Optional[str] = None,
        color: Optional[str] = None,
        owner_discord_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized = normalize_team_code(code or name)
        if not normalized:
            raise LeagueRuleError("INVALID", "Team codes need at least one letter or digit.")
        payload = {
            "code": normalized,
            "name": name.strip(),
            "emoji": emoji or None,
            "color": color or None,
            "owner_discord_id": owner_discord_id,
        }
        response = await run(self.supabase.table("teams").insert(payload))
        return response.data[0]

    async def update_team(self, team_id: str, **fields: Any) -> Dict[str, Any]:
        if fields.get("code"):
            fields["code"] = normalize_team_code(fields["code"])
        fields["updated_at"] = utc_now_iso()
        response = await run(self.supabase.table("teams").update(fields).eq("id", team_id))
        return (response.data or [{}])[0]

    async def archive_team(self, team_id: str, season_id: Optional[str]) -> None:
        """Retire a team: it leaves the current season (if it has no games) and stays in history."""
        if season_id:
            await self.remove_team_from_season(season_id, team_id)
        await self.update_team(team_id, archived_at=utc_now_iso(), owner_discord_id=None)

    # ------------------------------------------------------------------ season teams
    async def list_season_teams(
        self, season_id: str, division_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Teams in a season with their division and group. Each row has a nested ``teams`` record."""
        query = (
            self.supabase.table("season_teams")
            .select("season_id, team_id, division_id, group_id, teams(*)")
            .eq("season_id", season_id)
        )
        if division_id:
            query = query.eq("division_id", division_id)
        response = await run(query)
        rows = response.data or []
        rows.sort(key=lambda r: ((r.get("teams") or {}).get("name") or "").lower())
        return rows

    async def get_season_team(self, season_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        response = await run(
            self.supabase.table("season_teams").select("*").eq("season_id", season_id).eq("team_id", team_id).limit(1)
        )
        return (response.data or [None])[0]

    async def assign_team(
        self, season_id: str, team_id: str, division_id: str, group_id: Optional[str] = None
    ) -> None:
        """Put a team in a division (and optional group) for a season, moving it if already assigned."""
        existing = await self.get_season_team(season_id, team_id)
        if existing:
            await run(
                self.supabase.table("season_teams")
                .update({"division_id": division_id, "group_id": group_id})
                .eq("season_id", season_id)
                .eq("team_id", team_id)
            )
        else:
            await run(
                self.supabase.table("season_teams").insert(
                    {"season_id": season_id, "team_id": team_id, "division_id": division_id, "group_id": group_id}
                )
            )

    async def remove_team_from_season(self, season_id: str, team_id: str) -> None:
        await run(
            self.supabase.table("season_teams").delete().eq("season_id", season_id).eq("team_id", team_id)
        )

    # ------------------------------------------------------------------ standings
    async def get_team_records(self, season_id: str, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = self.supabase.table("team_records").select("*").eq("season_id", season_id)
        if division_id:
            query = query.eq("division_id", division_id)
        response = await run(query)
        return response.data or []

    async def list_regular_matches(self, season_id: str, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = (
            self.supabase.table("matches")
            .select("id, team1_id, team2_id, team1_score, team2_score, division_id, played_at")
            .eq("season_id", season_id)
            .eq("stage", "regular")
            .eq("status", "final")
        )
        if division_id:
            query = query.eq("division_id", division_id)
        response = await run(query)
        return response.data or []

    async def get_standings(self, season_id: str, division_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = (
            self.supabase.table("standings")
            .select("*, teams(id, code, name, emoji, color, logo_url)")
            .eq("season_id", season_id)
            .order("rank")
        )
        if division_id:
            query = query.eq("division_id", division_id)
        response = await run(query)
        return response.data or []

    async def replace_standings(self, season_id: str, division_id: str, rows: Iterable[Dict[str, Any]]) -> None:
        """Upsert a division's ranked rows and drop rows for teams that left the division."""
        rows = list(rows)
        if rows:
            await run(self.supabase.table("standings").upsert(rows, on_conflict="season_id,team_id"))
        keep = [r["team_id"] for r in rows]
        query = self.supabase.table("standings").delete().eq("season_id", season_id).eq("division_id", division_id)
        if keep:
            query = query.not_.in_("team_id", keep)
        await run(query)

    # ------------------------------------------------------------------ postseason
    async def list_brackets(self, season_id: str) -> List[Dict[str, Any]]:
        response = await run(
            self.supabase.table("brackets")
            .select("*, bracket_entries(*), series(*)")
            .eq("season_id", season_id)
        )
        return response.data or []

    async def get_series(self, series_id: str) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("series").select("*").eq("id", series_id).limit(1))
        return (response.data or [None])[0]

    async def find_ready_series(self, season_id: str, team_a: str, team_b: str) -> Optional[Dict[str, Any]]:
        """The open series between two teams this season, if any."""
        response = await run(
            self.supabase.table("series")
            .select("*, brackets!inner(id, season_id, kind, division_id, lower_division_id)")
            .eq("brackets.season_id", season_id)
            .eq("status", "ready")
            .or_(
                f"and(team1_id.eq.{team_a},team2_id.eq.{team_b}),"
                f"and(team1_id.eq.{team_b},team2_id.eq.{team_a})"
            )
            .limit(1)
        )
        return (response.data or [None])[0]

    async def list_series_matches(self, series_id: str) -> List[Dict[str, Any]]:
        response = await run(
            self.supabase.table("matches").select("*").eq("series_id", series_id).order("played_at")
        )
        return response.data or []

    async def create_postseason(self, season_id: str, plan: Dict[str, Any]) -> int:
        response = await run(self.supabase.rpc("create_postseason", {"p_season_id": season_id, "p_plan": plan}))
        return int(response.data or 0)

    async def swap_bracket_seeds(self, bracket_id: str, team_a: str, team_b: str) -> None:
        await run(
            self.supabase.rpc(
                "swap_bracket_seeds", {"p_bracket_id": bracket_id, "p_team_a": team_a, "p_team_b": team_b}
            )
        )

    async def force_series_winner(self, series_id: str, team_id: Optional[str]) -> None:
        """Decide a series without games (forfeit), or clear a forced result with ``None``."""
        await run(self.supabase.table("series").update({"forced_winner_id": team_id}).eq("id", series_id))
        await run(self.supabase.rpc("recompute_series", {"p_series_id": series_id}))

    async def finalize_season(
        self, season_id: str, champion_team_id: Optional[str], movements: List[Dict[str, Any]]
    ) -> None:
        await run(
            self.supabase.rpc(
                "finalize_season",
                {"p_season_id": season_id, "p_champion_team_id": champion_team_id, "p_movements": movements},
            )
        )

    async def start_next_season(self, previous_season_id: str, name: str) -> str:
        response = await run(
            self.supabase.rpc("start_next_season", {"p_prev_season_id": previous_season_id, "p_name": name.strip()})
        )
        return response.data

    async def list_movements(self, season_id: str) -> List[Dict[str, Any]]:
        response = await run(self.supabase.table("season_movements").select("*").eq("season_id", season_id))
        return response.data or []

    # ------------------------------------------------------------------ matches (admin)
    async def get_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        response = await run(self.supabase.table("matches").select("*").eq("id", match_id).limit(1))
        return (response.data or [None])[0]

    async def list_recent_matches(self, season_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        response = await run(
            self.supabase.table("matches")
            .select("*")
            .eq("season_id", season_id)
            .order("played_at", desc=True)
            .limit(limit)
        )
        return response.data or []

    async def void_match(self, match_id: str, voided_by: int) -> None:
        await run(
            self.supabase.table("matches").update({"status": "void", "voided_by": voided_by}).eq("id", match_id)
        )
