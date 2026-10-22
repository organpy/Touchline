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

    @app_commands.command(name="player-card", description="[STATS] Show a player's stats card with rating")
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

            # Fetch player stats for the season
            stats = {}
            seasons_played = []
            awards = []
            teams_played = []
            
            if season_row:
                try:
                    player_stats = await queries.get_player_stats_for_season(
                        self.supabase, player_id, season_row['id']
                    )
                    if player_stats:
                        # Aggregate stats
                        total_goals = sum(s.get('goals', 0) or 0 for s in player_stats)
                        total_assists = sum(s.get('assists', 0) or 0 for s in player_stats)
                        total_saves = sum(s.get('saves', 0) or 0 for s in player_stats)
                        total_points = sum(s.get('points', 0) or 0 for s in player_stats)
                        total_blocks = sum(s.get('blocks', 0) or 0 for s in player_stats)
                        total_own_goals = sum(s.get('own_goals', 0) or 0 for s in player_stats)
                        total_pp_plays = sum(s.get('powerup_plays', 0) or 0 for s in player_stats)
                        total_pp_used = sum(s.get('powerups_used', 0) or 0 for s in player_stats)
                        games_played = len(player_stats)

                        powerup_percentage = (total_pp_plays / total_pp_used * 100) if total_pp_used > 0 else 0

                        stats = {
                            'goals': total_goals,
                            'assists': total_assists,
                            'saves': total_saves,
                            'points': total_points,
                            'games_played': games_played,
                            'blocks': total_blocks,
                            'own_goals': total_own_goals,
                            'powerup_plays': total_pp_plays,
                            'powerups_used': total_pp_used,
                            'powerup_percentage': round(powerup_percentage, 2),
                        }
                except Exception as e:
                    logger.error(f"Failed to fetch player stats: {e}")
            
            # TODO: Fetch seasons played and teams played from rosters
            # For now, keep empty
            seasons_played = []
            teams_played = []
            
            # Generate the card image
            # Get avatar URL from Discord user if available
            avatar_url = None
            if player_record.get('discord_id'):
                try:
                    user = await self.bot.fetch_user(int(player_record['discord_id']))
                    if user and user.avatar:
                        avatar_url = user.avatar.url
                except Exception:
                    pass

            season_name = season_row.get('name', 'Unknown') if season_row else 'N/A'
            
            card_image = await generate_player_card(
                player_name=player_name,
                team_name=team_name,
                team_code=team_code,
                league_type=league_code,
                season_name=season_name,
                avatar_url=avatar_url,
                stats=stats,
                seasons=seasons_played,
                awards=awards,
                teams_played=teams_played,
            )
            
            if not card_image:
                await interaction.followup.send(f"❌ Failed to generate player card for {player_name}")
                return
            
            # Send the card
            file = discord.File(io.BytesIO(card_image), filename=f"player_card_{player_name.replace(' ', '_')}.png")
            
            # Add a simple embed with context
            embed = discord.Embed(
                title=f"🎴 {player_name}",
                description=f"**Team:** {team_name or 'Free Agent'}\n**Season:** {season_name}",
                color=discord.Color.gold(),
            )
            
            await interaction.followup.send(embed=embed, file=file)
        
        except Exception as e:
            logger.error(f"Error generating player card: {e}", exc_info=True)
            await interaction.followup.send(f"❌ {friendly_error(e, 'Something went wrong. The error was logged; please try again.')}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayerCardCog(bot))
    logger.info("✅ PlayerCardCog loaded")
