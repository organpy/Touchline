"""Team overview command: /team-overview."""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands

from db import queries
from cogs.stats.base import StatsCogBase
from cogs.stats.autocomplete import (
    _team_autocomplete,
    _league_autocomplete,
    _season_autocomplete,
)
from utils.embed_builder import EmbedBuilder
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


class TeamOverviewCog(StatsCogBase):
    """Team overview command."""

    @app_commands.command(name="team-overview", description="[STATS] Show team position, record, and roster for a season")
    @app_commands.describe(
        team="Team code or name",
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        team=_team_autocomplete,
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def team_overview(
        self,
        interaction: discord.Interaction,
        team: str,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # 1. Resolve Team
        team_cache = TeamPlayerCache()
        team_data = team_cache.get_team_by_code(team)
        if not team_data:
            # Try DB fallback if cache misses (unlikely if autocomplete worked)
            team_data = await queries.get_team_by_code(self.supabase, team)
        
        if not team_data:
            await interaction.followup.send(f"❌ Team `{team}` not found.")
            return

        team_code = team_data.get('code')
        team_name = team_data.get('name')
        team_id = team_data.get('id')

        # 2. Resolve season; the division is the one the team played in that season
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            team_id=team_id,
        )

        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        season_id = season_row['id']
        season_name = season_row['name']
        if not league_meta:
            await interaction.followup.send(f"ℹ️ **{team_name}** didn't play in **{season_name}**.")
            return
        league_code = league_meta.get('code')
        league_name = league_meta.get('name')

        # 3-4. Division standings (already ranked)
        standings = await queries.get_standings(self.supabase, season_id, division_id=league_meta['id'])
        team_standing = next((s for s in standings if s.get('team_id') == team_id), None)

        if not team_standing:
            rank_display = "N/A"
            stats_display = "No games played in this season."
        else:
            from utils.formatting import number_to_ordinal
            rank_display = f"{number_to_ordinal(team_standing['rank'])} of {len(standings)}"
            from services.standings import ZONE_EMOJI, ZONE_LABELS
            if team_standing.get('zone') in ZONE_LABELS:
                zone = team_standing['zone']
                rank_display += f" · {ZONE_EMOJI[zone]} {ZONE_LABELS[zone]}"
            gp = team_standing.get('gp', 0)
            wins = team_standing.get('wins', 0)
            losses = team_standing.get('losses', 0)
            points = team_standing.get('competition_points', 0)
            pf = team_standing.get('points_for', 0)
            pa = team_standing.get('points_against', 0)
            diff = team_standing.get('point_diff', 0)
            stats_display = (
                f"**Record**: {wins}-{losses} ({gp} GP)\n"
                f"**Points**: {points}\n"
                f"**Scoring**: {pf} PF / {pa} PA ({diff:+d})"
            )

        # 5. Fetch Roster
        # Try cache first for speed
        roster_players = team_cache.get_team_players(team_id)
        
        # Filter by season if cache includes historical data? 
        # Currently cache is "active" roster. If requesting historical season, need DB query.
        is_active_season = season_row.get('active', False)
        
        if not is_active_season:
            # Fetch historical roster from DB
            roster_rows = await queries.get_roster_for_team_season(self.supabase, team_id, season_id)
            # roster_rows usually returns joined player objects
            roster_names = [r.get('players', {}).get('display_name', 'Unknown') for r in roster_rows]
        else:
            # Use cache for active season
            roster_names = [p.get('display_name', 'Unknown') for p in roster_players]

        roster_display = "\n".join(f"• {name}" for name in sorted(roster_names)) or "No players on roster."

        # 6. Build Embed (team color + logo only, no division logo)
        builder = EmbedBuilder.generic(
            title=f"Team Overview — {team_name}",
            team_code=team_code,
            division_code=league_code,
        )
        
        builder.add_field("Division", f"{league_name} ({league_code})", inline=True)
        builder.add_field("Season", season_name, inline=True)
        builder.add_field("Position", rank_display, inline=True)
        
        if team_standing:
            builder.add_field("Season Stats", stats_display, inline=False)
        
        builder.add_field(f"Roster ({len(roster_names)})", roster_display, inline=False)
        
        # Show ONLY team logo as thumbnail (no division logo)
        if team_code:
            builder.add_team_image(team_code, position="thumbnail")

        embed, files = builder.build_with_files()
        await interaction.followup.send(embed=embed, files=files or None)


async def setup(bot):
    """Register the cog."""
    await bot.add_cog(TeamOverviewCog(bot))

