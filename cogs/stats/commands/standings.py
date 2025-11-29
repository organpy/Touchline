"""
Standings cog: /standings and /standings-simple commands
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from cogs.stats.base import StatsCogBase
from cogs.stats.autocomplete import (
    _league_autocomplete,
    _season_autocomplete,
)
from utils.standings_image_generator import generate_standings_image
from utils.simple_standings_image_generator import generate_simple_standings_image

logger = logging.getLogger(__name__)


class StandingsCog(StatsCogBase):
    """Standings command: /standings (now uses simple compact view)"""

    @app_commands.command(name="standings", description="[STATS] Show current season standings for a league")
    @app_commands.describe(
        league="League code or name",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def standings(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
        """Compact standings image with W, L, PF, PA, Diff, and last 5 results."""
        await interaction.response.defer(thinking=True)

        # Resolve season
        season_row, league_meta = await self._resolve_season_context(
            league_input=league,
            season_input=season,
            default_season_id=None,
            default_league_code=None,
        )
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        league_code = ((league_meta or {}).get('code') or season_row.get('league_type') or '').upper()
        
        # Fetch standings
        try:
            standings_data = await queries.get_standings(
                self.supabase,
                season_row['id'],
                league_type=league_code or None,
            )
        except Exception as exc:
            logger.error(f"Failed to fetch standings for season {season_row['id']}: {exc}")
            standings_data = None

        if not standings_data:
            league_season_display = self._format_league_season_display(league_meta, season_row)
            await interaction.followup.send(
                f"ℹ️ No standings recorded for {league_season_display} yet."
            )
            return

        season_league_display = self._format_league_display(league_meta)

        # Compute recent results markers per team (last 5)
        recent_map = {}
        for row in standings_data:
            team_id = row.get('team_id') or row.get('id')
            if not team_id:
                continue
            recent = await queries.get_team_recent_matches(self.supabase, season_row['id'], team_id, limit=5)
            markers = []
            for m in recent:
                t1 = m.get('team1_id')
                t2 = m.get('team2_id')
                s1 = m.get('team1_score', 0)
                s2 = m.get('team2_score', 0)
                if s1 == 0 and s2 == 0:
                    markers.append('N')
                else:
                    if (t1 == team_id and s1 > s2) or (t2 == team_id and s2 > s1):
                        markers.append('W')
                    else:
                        markers.append('L')
            while len(markers) < 5:
                markers.append('N')
            recent_map[team_id] = markers

        # Generate simple standings image
        standings_image = generate_simple_standings_image(
            standings_data,
            season_row.get('name', 'Unknown'),
            season_league_display,
            league_code=league_code,
            recent_results=recent_map,
        )

        if standings_image:
            standings_file = discord.File(standings_image, filename="standings.png")
            await interaction.followup.send(file=standings_file)
        else:
            # Fallback to text
            header = f"Standings — {season_league_display} ({season_row.get('name', 'Unknown')})"
            lines = [header, "```", "Rk Team         W  L   PF   PA  ±"]
            lines.append("-" * 38)
            for rank, team in enumerate(standings_data, 1):
                name = team.get('name', '?')[:10].ljust(10)
                w = str(team.get('wins', 0)).rjust(2)
                l = str(team.get('losses', 0)).rjust(2)
                pf = str(team.get('points_for', 0)).rjust(4)
                pa = str(team.get('points_against', 0)).rjust(4)
                diff = team.get('point_diff', 0)
                diff_str = f"{diff:+d}".rjust(4) if diff != 0 else "   0"
                lines.append(f"{rank:<2} {name} {w} {l} {pf} {pa} {diff_str}")
            lines.append("```")
            await interaction.followup.send("\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(StandingsCog(bot))
