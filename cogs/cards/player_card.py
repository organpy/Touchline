"""
Player Card cog: /player-card command
Shows a player's stats in a visual card format with star rating.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from db.league_repository import friendly_error
from utils.command_limits import HEAVY
from utils.player_card_image_generator import generate_player_card
from utils.team_player_cache import TeamPlayerCache
from utils.league_cache import LeagueCache

logger = logging.getLogger(__name__)


class PlayerCardCog(commands.Cog):
    """Player Card command: /player-card"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.supabase = get_supabase()

    async def _player_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for player names."""
        try:
            cache = TeamPlayerCache()
            all_players = cache.get_all_players()
            
            if not current:
                return [
                    app_commands.Choice(name=p.get('display_name', 'Unknown')[:100], value=p.get('id', ''))
                    for p in all_players[:25]
                ]
            
            current_lower = current.lower()
            matches = [
                p for p in all_players
                if current_lower in (p.get('display_name', '') or '').lower()
            ]
            
            return [
                app_commands.Choice(name=p.get('display_name', 'Unknown')[:100], value=p.get('id', ''))
                for p in matches[:25]
            ]
        except Exception as e:
            logger.error(f"Autocomplete error: {e}")
            return []

    @app_commands.command(name="player-card", description="[STATS] Show a player's stats card with rating", extras=HEAVY)
    @app_commands.describe(
        player="Player name or ID",
        season="Season name (e.g., S10). Defaults to active season",
    )
    @app_commands.autocomplete(player=_player_autocomplete)
    async def player_card_command(
        self,
        interaction: discord.Interaction,
        player: str,
        season: Optional[str] = None,
    ) -> None:
        """Generate and display a player's stats card."""
        await interaction.response.defer(thinking=True)
        
        try:
            # Look up player
            cache = TeamPlayerCache()
            player_record = cache.get_player_by_id(player)
            
            if not player_record:
                # Try by name
                player_record = cache.get_player_by_name(player)
            
            if not player_record:
                await interaction.followup.send(f"❌ Player not found: `{player}`")
                return
            
            player_id = player_record.get('id')
            player_name = player_record.get('display_name', 'Unknown')
            
            # Get player's current team
            team_info = cache.get_team_by_player_id(player_id)
            team_name = team_info.get('name') if team_info else None
            team_code = team_info.get('code') if team_info else None
            league_code = team_info.get('league_type') if team_info else None
            
            # Season by name/number, else the active one
            await LeagueCache.ensure_ready()
            season_row = LeagueCache.find_season(season) if season else None
            if not season_row:
                season_row = LeagueCache.active_season()

            # Style the card with the division the player's team played in that season
            if season_row and team_info:
                await LeagueCache.load_season_teams(season_row['id'])
                league_code = (LeagueCache.team_division(team_info['id'], season_row['id']) or {}).get('code') or league_code

            # One query for every season: this season's column and the career column both come from it
            career = await queries.get_player_career(self.supabase, player_id)
            season_stats = None
            if season_row:
                season_stats = next((row for row in career if row['season_id'] == season_row['id']), None)

            season_name = season_row.get('name', 'Unknown') if season_row else 'N/A'

            card_image = await generate_player_card(
                player_name=player_name,
                team_name=team_name,
                team_code=team_code,
                league_type=league_code,
                season_name=season_name,
                season_stats=season_stats,
                career=career,
            )
            
            if not card_image:
                await interaction.followup.send(f"❌ Failed to generate player card for {player_name}")
                return
            
            # Send the card
            file = discord.File(io.BytesIO(card_image), filename=f"player_card_{player_name.replace(' ', '_')}.png")
            
            await interaction.followup.send(file=file)
        
        except Exception as e:
            logger.error(f"Error generating player card: {e}", exc_info=True)
            await interaction.followup.send(f"❌ {friendly_error(e, 'Something went wrong. The error was logged; please try again.')}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayerCardCog(bot))
    logger.info("✅ PlayerCardCog loaded")
