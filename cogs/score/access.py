"""
Access control for reporting match results.
Only admins and team owners may report; owners only for matches involving their own team.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Optional

import discord

from db import queries
from utils.config_utility import ConfigUtility

NOT_ALLOWED_MESSAGE = "❌ Only team owners and admins can report match results."
NOT_YOUR_MATCH_MESSAGE = "❌ You can only report matches involving a team you own."


@dataclass(frozen=True)
class ReporterAccess:
    """What the reporting user is allowed to report."""

    is_admin: bool
    owned_team_ids: FrozenSet[str] = frozenset()

    def can_report(self, team1_id: Any, team2_id: Any) -> bool:
        if self.is_admin:
            return True
        return str(team1_id) in self.owned_team_ids or str(team2_id) in self.owned_team_ids


async def resolve_reporter_access(
    bot: Any,
    supabase: Any,
    interaction: discord.Interaction,
) -> Optional[ReporterAccess]:
    """Return the caller's reporting access, or None if they may not report results."""
    if not interaction.guild:
        return None

    guild_config = await ConfigUtility.get_guild_config(bot, interaction.guild.id)
    if await ConfigUtility.check_admin_permissions(interaction, guild_config):
        return ReporterAccess(is_admin=True)

    teams = await queries.get_teams_by_owner(supabase, interaction.user.id)
    owned_team_ids = frozenset(str(team['id']) for team in teams if team.get('id'))
    if not owned_team_ids:
        return None
    return ReporterAccess(is_admin=False, owned_team_ids=owned_team_ids)
