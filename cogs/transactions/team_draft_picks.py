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

    async def autocomplete_available_players(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for available players (not yet picked)."""
        try:
            # Get user's team
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            if not team:
                return []

            # Get active season
            try:
                seasons_response = self.supabase.table('seasons').select('*').eq(
                    'division_id', team['division_id']
                ).eq('active', True).limit(1).execute()
                if not seasons_response.data:
                    return []
                season = seasons_response.data[0]
            except Exception as e:
                logger.warning(f"Failed to get active season: {e}")
                return []

            # Get all players not yet picked in this season
            try:
                picked_response = self.supabase.table('draft_picks').select(
                    'selected_player_id'
                ).eq('season_id', season['id']).not_.is_('selected_player_id', 'null').execute()
                picked_ids = [p.get('selected_player_id') for p in (picked_response.data or []) if p.get('selected_player_id')]
            except Exception as e:
                logger.warning(f"Failed to get picked players: {e}")
                picked_ids = []

            # Get all players, filter out picked ones
            try:
                all_players_response = self.supabase.table('players').select('id, display_name').execute()
                all_players = all_players_response.data or []
                available = [p for p in all_players if p.get('id') not in picked_ids]
            except Exception as e:
                logger.warning(f"Failed to get available players: {e}")
                return []

            # Filter by current input (case-insensitive)
            current_lower = current.lower()
            filtered = [
                p for p in available
                if current_lower in (p.get('display_name') or '').lower()
            ]

            # Return up to 25 choices, sorted by name
            sorted_filtered = sorted(filtered, key=lambda p: p.get('display_name', ''))[:25]
            return [
                app_commands.Choice(
                    name=p.get('display_name', 'Unknown')[:100],
                    value=p.get('id', '')
                )
                for p in sorted_filtered
            ]

        except Exception as e:
            logger.error(f"Error in autocomplete_available_players: {e}")
            return []

    @app_commands.command(
        name="pick",
        description="Make your team's next draft pick"
    )
    @app_commands.describe(
        player_id="Player to pick (search by name)"
    )
    @app_commands.autocomplete(player_id=autocomplete_available_players)
    async def make_pick(
        self,
        interaction: discord.Interaction,
        player_id: str,
    ):
        """Team owner command to make a draft pick."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get user's team using queries function (not cache)
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            
            if not team:
                await interaction.followup.send(
                    "❌ You don't own any team. Contact an admin.",
                    ephemeral=True,
                )
                return

            # Get active season
            try:
                seasons_response = self.supabase.table('seasons').select('*').eq(
                    'division_id', team['division_id']
                ).eq('active', True).limit(1).execute()
                if not seasons_response.data:
                    await interaction.followup.send(
                        "❌ No active season for your division.",
                        ephemeral=True,
                    )
                    return
                season = seasons_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get active season: {e}")
                await interaction.followup.send(
                    "❌ Error getting season. Check server logs.",
                    ephemeral=True,
                )
                return

            # Get player by ID (from autocomplete)
            try:
                player_response = self.supabase.table('players').select('*').eq('id', player_id).limit(1).execute()
                if not player_response.data:
                    await interaction.followup.send(
                        "❌ Player not found.",
                        ephemeral=True,
                    )
                    return
                player = player_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get player: {e}")
                await interaction.followup.send(
                    "❌ Error fetching player. Check server logs.",
                    ephemeral=True,
                )
                return

            # Get team's next available pick
            try:
                picks_response = self.supabase.table('draft_picks').select('*').eq(
                    'season_id', season['id']
                ).eq(
                    'current_owner_team_id', team['id']
                ).is_('selected_player_id', 'null').order('overall', desc=False).limit(1).execute()
                
                if not picks_response.data:
                    await interaction.followup.send(
                        f"❌ {team['code']} has no available picks in {season['name']}.",
                        ephemeral=True,
                    )
                    return
                pick = picks_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get team picks: {e}")
                await interaction.followup.send(
                    "❌ Error fetching picks. Check server logs.",
                    ephemeral=True,
                )
                return

            # Check if player is already picked
            try:
                existing_response = self.supabase.table('draft_picks').select('*').eq(
                    'season_id', season['id']
                ).eq(
                    'selected_player_id', player['id']
                ).limit(1).execute()

                if existing_response.data:
                    await interaction.followup.send(
                        f"⚠️ Player **{player['display_name']}** has already been picked.",
                        ephemeral=True,
                    )
                    return
            except Exception as e:
                logger.warning(f"Failed to check if player already picked: {e}")

            # Update pick with player selection
            try:
                result = self.supabase.table('draft_picks').update({
                    'selected_player_id': player['id'],
                    'selected_at': datetime.utcnow().isoformat(),
                }).eq('id', pick['id']).execute()

                if result.data:
                    # Record transaction
                    try:
                        self.repo.insert_transaction({
                            'season_id': season['id'],
                            'transaction_type': 'draft_pick',
                            'player_id': player['id'],
                            'team_id': team['id'],
                            'metadata': {
                                'pick_number': pick.get('overall'),
                                'round': pick.get('round'),
                            },
                        })
                    except Exception as trans_error:
                        logger.warning(f"Failed to record transaction: {trans_error}")

                    embed = discord.Embed(
                        title="✅ Draft Pick Confirmed",
                        description=f"**{team['code']}** selected **{player['display_name']}** in round {pick.get('round')}, pick #{pick.get('overall')}",
                        color=discord.Color.green(),
                    )
                    embed.add_field(name="Season", value=season['name'], inline=True)
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
            # Get user's team using queries function (not cache)
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            
            if not team:
                await interaction.followup.send(
                    "❌ You don't own any team.",
                    ephemeral=True,
                )
                return

            # Get active season
            try:
                seasons_response = self.supabase.table('seasons').select('*').eq(
                    'division_id', team['division_id']
                ).eq('active', True).limit(1).execute()
                if not seasons_response.data:
                    await interaction.followup.send(
                        "❌ No active season.",
                        ephemeral=True,
                    )
                    return
                season = seasons_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get active season: {e}")
                await interaction.followup.send(
                    "❌ Error getting season. Check server logs.",
                    ephemeral=True,
                )
                return

            # Get all team picks
            try:
                picks_response = self.supabase.table('draft_picks').select(
                    '*, players(display_name)'
                ).eq(
                    'season_id', season['id']
                ).eq(
                    'current_owner_team_id', team['id']
                ).order('overall', desc=False).execute()

                if not picks_response.data:
                    await interaction.followup.send(
                        f"❌ No picks found for {team['code']}.",
                        ephemeral=True,
                    )
                    return
                
                picks = picks_response.data
            except Exception as e:
                logger.error(f"Failed to get team picks: {e}")
                await interaction.followup.send(
                    "❌ Error loading picks. Check server logs.",
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

            for pick in picks:
                if pick.get('selected_player_id'):
                    player_data = pick.get('players')
                    player_name = (player_data[0].get('display_name') if player_data else None) or "Unknown"
                    embed.add_field(
                        name=f"Round {pick.get('round')}, Pick #{pick.get('overall')} ✅",
                        value=f"Selected: **{player_name}**",
                        inline=False,
                    )
                    completed += 1
                else:
                    embed.add_field(
                        name=f"Round {pick.get('round')}, Pick #{pick.get('overall')} ⏳",
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
