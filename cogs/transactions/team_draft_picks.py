"""Team owner command to select/make draft picks."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional, Any, Dict, List
from datetime import datetime

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)


class TeamDraftPicksCog(commands.Cog):
    """Cog for team owners to make draft picks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)

    @app_commands.command(
        name="pick",
        description="Make your team's next draft pick"
    )
    @app_commands.describe(
        player_name="Player display name to pick"
    )
    async def make_pick(
        self,
        interaction: discord.Interaction,
        player_name: str,
    ):
        """Team owner command to make a draft pick."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get user's team
            cache = TeamPlayerCache()
            team = cache.get_team_by_owner(interaction.user.id)
            
            if not team:
                await interaction.followup.send(
                    "❌ You don't own any team. Contact an admin.",
                    ephemeral=True,
                )
                return

            # Get active season
            seasons = self.supabase.table('seasons').select('*').eq('division_id', team['division_id']).eq('active', True).execute()
            if not seasons.data:
                await interaction.followup.send(
                    "❌ No active season for your division.",
                    ephemeral=True,
                )
                return
            
            season = seasons.data[0]

            # Find player by name
            player = await queries.get_player_by_name(self.supabase, player_name)
            if not player:
                await interaction.followup.send(
                    f"❌ Player **{player_name}** not found.",
                    ephemeral=True,
                )
                return

            # Get team's next available pick
            picks = self.supabase.table('draft_picks').select('*').eq(
                'season_id', season['id']
            ).eq(
                'current_owner_team_id', team['id']
            ).is_('selected_player_id', 'null').order('overall', desc=False).limit(1).execute()

            if not picks.data:
                await interaction.followup.send(
                    f"❌ {team['code']} has no available picks in {season['name']}.",
                    ephemeral=True,
                )
                return

            pick = picks.data[0]

            # Check if player is already picked
            existing_picks = self.supabase.table('draft_picks').select('*').eq(
                'season_id', season['id']
            ).eq(
                'selected_player_id', player['id']
            ).execute()

            if existing_picks.data:
                await interaction.followup.send(
                    f"⚠️ Player **{player['display_name']}** has already been picked.",
                    ephemeral=True,
                )
                return

            # Update pick with player selection
            try:
                result = self.supabase.table('draft_picks').update({
                    'selected_player_id': player['id'],
                    'selected_at': datetime.utcnow().isoformat(),
                }).eq('id', pick['id']).execute()

                if result.data:
                    # Record transaction
                    self.repo.insert_transaction({
                        'season_id': season['id'],
                        'transaction_type': 'draft_pick',
                        'player_id': player['id'],
                        'team_id': team['id'],
                        'metadata': {
                            'pick_number': pick['overall'],
                            'round': pick['round'],
                        },
                    })

                    embed = discord.Embed(
                        title="✅ Draft Pick Confirmed",
                        description=f"**{team['code']}** selected **{player['display_name']}** in round {pick['round']}, pick #{pick['overall']}",
                        color=discord.Color.green(),
                    )
                    embed.add_field(name="Season", value=season['name'], inline=True)
                    embed.add_field(name="Player Position", value=player.get('position') or 'Not set', inline=True)
                    await interaction.followup.send(embed=embed, ephemeral=True)

                    # Refresh cache
                    try:
                        await TeamPlayerCache.refresh()
                    except Exception as cache_error:
                        logger.warning(f"Failed to refresh cache: {cache_error}")
                else:
                    await interaction.followup.send("❌ Failed to record pick. Try again.", ephemeral=True)

            except Exception as e:
                logger.error(f"Error updating pick: {e}", exc_info=True)
                await interaction.followup.send("❌ Error processing pick. Check server logs.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in make_pick: {e}", exc_info=True)
            await interaction.followup.send("❌ Error processing command.", ephemeral=True)

    @app_commands.command(
        name="picks",
        description="View your team's remaining draft picks"
    )
    async def view_picks(self, interaction: discord.Interaction):
        """Team owner command to view their remaining picks."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get user's team
            cache = TeamPlayerCache()
            team = cache.get_team_by_owner(interaction.user.id)
            
            if not team:
                await interaction.followup.send(
                    "❌ You don't own any team.",
                    ephemeral=True,
                )
                return

            # Get active season
            seasons = self.supabase.table('seasons').select('*').eq(
                'division_id', team['division_id']
            ).eq('active', True).execute()
            if not seasons.data:
                await interaction.followup.send(
                    "❌ No active season.",
                    ephemeral=True,
                )
                return
            
            season = seasons.data[0]

            # Get all team picks
            picks = self.supabase.table('draft_picks').select(
                '*, players(display_name, position)'
            ).eq(
                'season_id', season['id']
            ).eq(
                'current_owner_team_id', team['id']
            ).order('overall', desc=False).execute()

            if not picks.data:
                await interaction.followup.send(
                    f"❌ No picks found for {team['code']}.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title=f"📋 {team['code']} Draft Picks",
                description=f"Season: **{season['name']}**",
                color=discord.Color.blue(),
            )

            remaining = 0
            completed = 0

            for pick in picks.data:
                if pick.get('selected_player_id'):
                    player_name = pick['players'][0]['display_name'] if pick.get('players') else "Unknown"
                    embed.add_field(
                        name=f"Round {pick['round']}, Pick #{pick['overall']} ✅",
                        value=f"Selected: **{player_name}**",
                        inline=False,
                    )
                    completed += 1
                else:
                    embed.add_field(
                        name=f"Round {pick['round']}, Pick #{pick['overall']} ⏳",
                        value="Not yet selected",
                        inline=False,
                    )
                    remaining += 1

            embed.set_footer(text=f"Remaining: {remaining} | Completed: {completed}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in view_picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Error loading picks.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading."""
    await bot.add_cog(TeamDraftPicksCog(bot))
    logger.info("✅ TeamDraftPicksCog loaded")
