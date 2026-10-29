"""
Standings cog: /standings and /standings-simple commands
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import queries
from cogs.stats.base import StatsCogBase
from cogs.stats.autocomplete import (
    _league_autocomplete,
    _season_autocomplete,
)
from services.standings import ZONE_EMOJI, ZONE_LABELS
from utils.league_cache import LeagueCache
from utils.standings_image_generator import generate_simple_standings_image
from utils.render.limit import render_in_thread
from utils.command_limits import HEAVY

logger = logging.getLogger(__name__)


def zone_legend(rows: List[Dict[str, Any]]) -> str:
    """One line explaining the zone colours that appear in these standings."""
    present = [zone for zone in ZONE_LABELS if any(row.get('zone') == zone for row in rows)]
    return " · ".join(f"{ZONE_EMOJI[zone]} {ZONE_LABELS[zone]}" for zone in present)


class StandingsCog(StatsCogBase):
    """Standings command: /standings (uses compact standings view with recent results)"""

    @app_commands.command(name="standings", description="[STATS] Show season standings for a division", extras=HEAVY)
    @app_commands.describe(
        league="Division code or name (defaults to every division)",
        season="Season name or number. Defaults to the active season",
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
        """Compact standings image per division with W, L, PF, PA, Diff and the last 5 results."""
        await interaction.response.defer(thinking=True)

        season_row, league_meta = await self._resolve_season_context(league_input=league, season_input=season)
        if not season_row:
            await interaction.followup.send("❌ Could not locate a season matching your request.")
            return

        try:
            standings_data = await queries.get_standings(
                self.supabase,
                season_row['id'],
                division_id=(league_meta or {}).get('id'),
            )
        except Exception as exc:
            logger.error(f"Failed to fetch standings for season {season_row['id']}: {exc}")
            standings_data = []

        if not standings_data:
            league_season_display = self._format_league_season_display(league_meta, season_row)
            await interaction.followup.send(f"ℹ️ No standings recorded for {league_season_display} yet.")
            return

        by_division: Dict[str, List[Dict[str, Any]]] = {}
        for row in standings_data:
            by_division.setdefault(row['division_id'], []).append(row)

        recent_map = await self._recent_results(season_row['id'], standings_data)
        files: List[discord.File] = []
        text_blocks: List[str] = []
        for division_id, rows in by_division.items():
            division = LeagueCache.division(division_id) or {}
            division_display = self._format_league_display(division)
            image = await render_in_thread(
                generate_simple_standings_image,
                rows,
                season_row.get('name', 'Unknown'),
                division_display,
                league_code=division.get('code'),
                recent_results={r['team_id']: recent_map.get(r['team_id'], []) for r in rows},
                accent_color=division.get('color'),
            )
            if image:
                files.append(discord.File(image, filename=f"standings-{(division.get('code') or 'division').lower()}.png"))
            else:
                text_blocks.append(self._text_table(rows, division_display, season_row))

        legend = zone_legend(standings_data)
        # Discord allows 10 attachments per message.
        for start in range(0, len(files), 10):
            await interaction.followup.send(content=legend if start == 0 and legend else None, files=files[start:start + 10])
        for block in text_blocks:
            await interaction.followup.send(block)

    async def _recent_results(self, season_id: str, rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Last five regular-season results per team as 'W'/'L' markers, padded with 'N'."""
        results: Dict[str, List[str]] = {}
        for row in rows:
            team_id = row.get('team_id')
            if not team_id:
                continue
            recent = await queries.get_team_recent_matches(self.supabase, season_id, team_id, limit=5)
            markers = []
            for match in recent:
                if match.get('stage', 'regular') != 'regular':
                    continue
                own, other = (
                    (match.get('team1_score', 0), match.get('team2_score', 0))
                    if match.get('team1_id') == team_id
                    else (match.get('team2_score', 0), match.get('team1_score', 0))
                )
                markers.append('W' if own > other else 'L')
            results[team_id] = (markers + ['N'] * 5)[:5]
        return results

    @staticmethod
    def _text_table(rows: List[Dict[str, Any]], division_display: str, season_row: Dict[str, Any]) -> str:
        lines = [f"Standings — {division_display} ({season_row.get('name', 'Unknown')})", "```",
                 "Rk Team         W  L   PF   PA    ±"]
        lines.append("-" * 40)
        for row in rows:
            name = (row.get('team_name') or '?')[:10].ljust(10)
            zone = ZONE_EMOJI.get(row.get('zone'), '')
            diff = row.get('point_diff', 0)
            lines.append(
                f"{row.get('rank', '?'):<2} {name} {row.get('wins', 0):>2} {row.get('losses', 0):>2} "
                f"{row.get('points_for', 0):>4} {row.get('points_against', 0):>4} {diff:>+5d} {zone}"
            )
        lines.append("```")
        return "\n".join(lines)


async def setup(bot: commands.Bot) -> None:
    """Setup hook for auto-discovery."""
    await bot.add_cog(StandingsCog(bot))
