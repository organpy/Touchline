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
from utils.player_card_image_generator import generate_player_card, calculate_star_rating
from utils.team_player_cache import TeamPlayerCache

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
            
            # Get season (default to active)
            division_id = team_info.get('division_id') if team_info else None
            season_row = None
            
            if season:
                # Try to find specific season
                seasons = await queries.get_all_seasons(self.supabase)
                for s in seasons:
                    if s.get('name', '').lower() == season.lower():
                        season_row = s
                        break
            
            if not season_row:
                # Get active season for division
                season_row = await queries.get_active_season(self.supabase, division_id=division_id)
            
            if not season_row:
                # Fallback to any active season
                season_row = await queries.get_active_season(self.supabase)
            
            # Fetch player stats for the season
            stats = {}
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
                        games_played = len(player_stats)
                        
                        ppg = total_points / games_played if games_played > 0 else 0
                        
                        stats = {
                            'ppg': ppg,
                            'games': games_played,
                            'avg_goals': total_goals / max(games_played, 1),
                            'avg_assists': total_assists / max(games_played, 1),
                            'avg_saves': total_saves / max(games_played, 1),
                            'total_points': total_points,
                        }
                except Exception as e:
                    logger.error(f"Failed to fetch player stats: {e}")
            
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
            
            card_image = await generate_player_card(
                player_name=player_name,
                team_name=team_name or 'Free Agent',
                team_code=team_code or 'FA',
                league_type=league_code or 'NBL',
                avatar_url=avatar_url,
                stats=stats,
            )
            
            if not card_image:
                await interaction.followup.send(f"❌ Failed to generate player card for {player_name}")
                return
            
            # Send the card
            file = discord.File(io.BytesIO(card_image), filename=f"player_card_{player_name.replace(' ', '_')}.png")
            
            # Add a simple embed with context
            star_rating = calculate_star_rating(stats)
            season_name = season_row.get('name', 'Unknown') if season_row else 'N/A'
            
            embed = discord.Embed(
                title=f"⭐ {player_name}",
                description=f"**Team:** {team_name or 'Free Agent'}\n**Season:** {season_name}\n**Rating:** {'⭐' * int(star_rating)}{'✨' if star_rating % 1 >= 0.5 else ''} ({star_rating:.1f}/5.0)",
                color=discord.Color.gold(),
            )
            
            await interaction.followup.send(embed=embed, file=file)
        
        except Exception as e:
            logger.error(f"Error generating player card: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayerCardCog(bot))
    logger.info("✅ PlayerCardCog loaded")
