"""
Match Table cog: /match-table command
Shows all games played in a season with scores in a table format.
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
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)


class MatchTableCog(StatsCogBase):
    """Match Table command: /match-table"""

    @app_commands.command(name="match-table", description="[STATS] Show all games played this season with scores")
    @app_commands.describe(
        league="League code or name (defaults to active season league)",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(
        league=_league_autocomplete,
        season=_season_autocomplete,
    )
    async def match_table(
        self,
        interaction: discord.Interaction,
        league: Optional[str] = None,
        season: Optional[str] = None,
    ) -> None:
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

        league_season_display = self._format_league_season_display(league_meta, season_row)
        league_code = ((league_meta or {}).get('code') or season_row.get('league_type') or '').upper()

        # Fetch matches
        try:
            matches = await queries.get_matches_for_season(self.supabase, season_row['id'])
        except Exception as exc:
            logger.error(f"Failed to fetch matches for season {season_row['id']}: {exc}")
            matches = None

        if not matches:
            await interaction.followup.send(
                f"ℹ️ No matches recorded for {league_season_display} yet."
            )
            return

        # Build the match table
        table_lines = []
        
        # Header
        table_lines.append("```")
        table_lines.append(f"{'#':<3} {'Home':<20} {'Score':^9} {'Away':<20}")
        table_lines.append("-" * 55)
        
        for idx, match in enumerate(matches, 1):
            team1 = match.get('team1') or {}
            team2 = match.get('team2') or {}
            
            team1_name = team1.get('name', 'Unknown')[:18]
            team2_name = team2.get('name', 'Unknown')[:18]
            
            score1 = match.get('team1_score', 0) or 0
            score2 = match.get('team2_score', 0) or 0
            
            score_str = f"{score1:>3} - {score2:<3}"
            
            table_lines.append(f"{idx:<3} {team1_name:<20} {score_str:^9} {team2_name:<20}")
        
        table_lines.append("```")
        
        table_content = "\n".join(table_lines)
        
        # Check if content is too long - split into multiple embeds if needed
        if len(table_content) > 4000:
            # Split matches into chunks
            chunk_size = 20
            chunks = [matches[i:i + chunk_size] for i in range(0, len(matches), chunk_size)]
            
            for chunk_idx, chunk in enumerate(chunks):
                chunk_lines = ["```"]
                chunk_lines.append(f"{'#':<3} {'Home':<20} {'Score':^9} {'Away':<20}")
                chunk_lines.append("-" * 55)
                
                start_idx = chunk_idx * chunk_size + 1
                for idx, match in enumerate(chunk, start_idx):
                    team1 = match.get('team1') or {}
                    team2 = match.get('team2') or {}
                    
                    team1_name = team1.get('name', 'Unknown')[:18]
                    team2_name = team2.get('name', 'Unknown')[:18]
                    
                    score1 = match.get('team1_score', 0) or 0
                    score2 = match.get('team2_score', 0) or 0
                    
                    score_str = f"{score1:>3} - {score2:<3}"
                    
                    chunk_lines.append(f"{idx:<3} {team1_name:<20} {score_str:^9} {team2_name:<20}")
                
                chunk_lines.append("```")
                chunk_content = "\n".join(chunk_lines)
                
                # Build embed for this chunk
                title = f"🏀 Match Results — {league_season_display}"
                if len(chunks) > 1:
                    title += f" (Page {chunk_idx + 1}/{len(chunks)})"
                
                builder = EmbedBuilder(
                    title=title,
                    description=chunk_content,
                    division_code=league_code,
                )
                
                if chunk_idx == 0:
                    builder.add_field("Total Matches", str(len(matches)), inline=True)
                
                builder.add_timestamp()
                embed = builder.build()
                
                if chunk_idx == 0:
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send(embed=embed)
        else:
            # Single embed for all matches
            builder = EmbedBuilder(
                title=f"🏀 Match Results — {league_season_display}",
                description=table_content,
                division_code=league_code,
            )
            builder.add_field("Total Matches", str(len(matches)), inline=True)
            builder.add_timestamp()
            embed = builder.build()
            
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchTableCog(bot))
