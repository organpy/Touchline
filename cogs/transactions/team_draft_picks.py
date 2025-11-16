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
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)

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
        """Autocomplete for available players (not yet picked). Only shows non-NBL players."""
        try:
            # Get user's team
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            if not team:
                return []

            # Get active season for this division
            try:
                seasons_response = self.supabase.table('seasons').select('id').eq(
                    'division_id', team['division_id']
                ).eq('active', True).limit(1).execute()
                if not seasons_response.data:
                    return []
                season_id = seasons_response.data[0]['id']
            except Exception as e:
                logger.warning(f"Failed to get active season: {e}")
                return []

            # ONE QUERY: Get available players (non-NBL, on roster, not picked)
            # Using RPC or direct query to join: teams (non-NBL) -> rosters -> players -> exclude drafted
            try:
                # Get all available players from non-NBL rosters in this season
                response = self.supabase.table('rosters').select(
                    'players(id, display_name)'
                ).eq('season_id', season_id).is_(
                    'left_at', 'null'
                ).execute()
                
                # Extract player IDs from rosters
                roster_player_ids = set()
                for roster in (response.data or []):
                    player_data = roster.get('players')
                    if player_data and isinstance(player_data, dict):
                        roster_player_ids.add(player_data['id'])

                if not roster_player_ids:
                    return []

            except Exception as e:
                logger.warning(f"Failed to get rosters: {e}")
                return []

            # Get picked player IDs (already drafted this season)
            try:
                picked_response = self.supabase.table('draft_picks').select(
                    'selected_player_id'
                ).eq('season_id', season_id).not_.is_('selected_player_id', 'null').execute()
                picked_ids = {p['selected_player_id'] for p in (picked_response.data or [])}
            except Exception as e:
                logger.warning(f"Failed to get picked players: {e}")
                picked_ids = set()

            # Filter by: on roster + not picked yet + matches search
            available_ids = roster_player_ids - picked_ids
            if not available_ids:
                return []

            # Get full player details for available players (only fetch what we need)
            try:
                all_players_response = self.supabase.table('players').select(
                    'id, display_name'
                ).in_('id', list(available_ids)).execute()
                all_players = all_players_response.data or []
            except Exception as e:
                logger.warning(f"Failed to get available players: {e}")
                return []

            # Filter by current input (case-insensitive)
            current_lower = current.lower()
            filtered = [
                p for p in all_players
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
        description="[TEAM OWNER] Make your team's next draft pick"
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
            # Get user's team
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

            # Get team's next available pick (only unpicked picks)
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

            # Check if player is already picked (avoid duplicates)
            try:
                existing_response = self.supabase.table('draft_picks').select('id').eq(
                    'season_id', season['id']
                ).eq(
                    'selected_player_id', player_id
                ).limit(1).execute()

                if existing_response.data:
                    await interaction.followup.send(
                        f"⚠️ Player has already been picked.",
                        ephemeral=True,
                    )
                    return
            except Exception as e:
                logger.warning(f"Failed to check if player already picked: {e}")

            # Update pick with player selection
            try:
                result = self.supabase.table('draft_picks').update({
                    'selected_player_id': player_id,
                }).eq('id', pick['id']).execute()

                if not result.data:
                    await interaction.followup.send(
                        "❌ Failed to record pick. Try again.",
                        ephemeral=True,
                    )
                    return

                # Get player display name
                try:
                    player_response = self.supabase.table('players').select('display_name').eq('id', player_id).limit(1).execute()
                    player_name = player_response.data[0]['display_name'] if player_response.data else 'Unknown'
                except Exception as e:
                    logger.warning(f"Failed to get player name: {e}")
                    player_name = 'Unknown'

                # Success
                await interaction.followup.send(
                    f"✅ **Draft Pick Confirmed** (Round {pick.get('round')}, Overall {pick.get('overall')})\n"
                    f"Team: **{team['code']}**\n"
                    f"Player: **{player_name}**",
                    ephemeral=True,
                )

            except Exception as e:
                logger.error(f"Failed to update pick: {e}")
                await interaction.followup.send(
                    "❌ Error recording pick. Check server logs.",
                    ephemeral=True,
                )
                return

        except Exception as e:
            logger.error(f"Unexpected error in make_pick: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Unexpected error occurred. Check server logs.",
                ephemeral=True,
            )

    @app_commands.command(
        name="picks",
        description="[TEAM OWNER] View your team's remaining draft picks"
    )
    async def view_picks(self, interaction: discord.Interaction):
        """Team owner command to view their remaining picks."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get user's team
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
                picks_response = self.supabase.table('draft_picks').select('*').eq(
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
                    # Get player name for selected pick
                    try:
                        player_response = self.supabase.table('players').select('display_name').eq(
                            'id', pick['selected_player_id']
                        ).limit(1).execute()
                        player_name = player_response.data[0]['display_name'] if player_response.data else "Unknown"
                    except Exception:
                        player_name = "Unknown"
                    
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
            files, thumbnail = build_team_logo_payload(team['code'])
            if thumbnail:
                target_thumbnail(embed, thumbnail)
            apply_team_badge(embed, team['code'])
            league_code = (season.get('league_type') or '').upper() or None
            apply_league_badge(embed, league_code)

            await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)

        except Exception as e:
            logger.error(f"Error in view_picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Error loading picks.", ephemeral=True)

    # ------------------------------------------------------------------
    # Admin force pick (bypass all validation)
    # ------------------------------------------------------------------
    @app_commands.command(
        name="admin-force-pick",
        description="[ADMIN] Admin: Force assign a draft pick to a player"
    )
    @app_commands.describe(
        team_code="Team code (e.g., PANDAS)",
        player_name="Player display name",
        pick_number="Overall pick number (optional: auto-select next available)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_force_pick(
        self,
        interaction: discord.Interaction,
        team_code: str,
        player_name: str,
        pick_number: Optional[int] = None
    ) -> None:
        """Admin can force assign a draft pick to any player without validation."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get active season
            try:
                seasons_response = self.supabase.table('seasons').select('*').eq(
                    'active', True
                ).limit(1).execute()
                if not seasons_response.data:
                    await interaction.followup.send(
                        "❌ No active season found.",
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

            # Resolve team by code
            try:
                team_response = self.supabase.table('teams').select('*').ilike(
                    'code', team_code
                ).limit(1).execute()
                if not team_response.data:
                    await interaction.followup.send(
                        f"❌ Team code `{team_code}` not found.",
                        ephemeral=True,
                    )
                    return
                team = team_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get team: {e}")
                await interaction.followup.send(
                    "❌ Error getting team. Check server logs.",
                    ephemeral=True,
                )
                return

            # Resolve player by name
            try:
                player_response = self.supabase.table('players').select('*').ilike(
                    'display_name', player_name
                ).limit(1).execute()
                if not player_response.data:
                    await interaction.followup.send(
                        f"❌ Player `{player_name}` not found.",
                        ephemeral=True,
                    )
                    return
                player = player_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get player: {e}")
                await interaction.followup.send(
                    "❌ Error getting player. Check server logs.",
                    ephemeral=True,
                )
                return

            # Get or create the pick
            try:
                if pick_number:
                    # Use specific pick number
                    pick_response = self.supabase.table('draft_picks').select('*').eq(
                        'season_id', season['id']
                    ).eq(
                        'overall', pick_number
                    ).limit(1).execute()
                    
                    if not pick_response.data:
                        await interaction.followup.send(
                            f"❌ Pick #{pick_number} not found in {season['name']}.",
                            ephemeral=True,
                        )
                        return
                    pick = pick_response.data[0]
                else:
                    # Find next available pick for team
                    pick_response = self.supabase.table('draft_picks').select('*').eq(
                        'season_id', season['id']
                    ).eq(
                        'current_owner_team_id', team['id']
                    ).is_(
                        'selected_player_id', 'null'
                    ).order('overall', desc=False).limit(1).execute()
                    
                    if not pick_response.data:
                        await interaction.followup.send(
                            f"❌ {team['code']} has no available picks in {season['name']}.",
                            ephemeral=True,
                        )
                        return
                    pick = pick_response.data[0]
            except Exception as e:
                logger.error(f"Failed to get pick: {e}")
                await interaction.followup.send(
                    "❌ Error fetching pick. Check server logs.",
                    ephemeral=True,
                )
                return

            # Update the pick (force assign regardless of state)
            try:
                result = self.supabase.table('draft_picks').update({
                    'selected_player_id': player['id'],
                }).eq('id', pick['id']).execute()

                if not result.data:
                    await interaction.followup.send(
                        "❌ Failed to record pick. Try again.",
                        ephemeral=True,
                    )
                    return

                # Refresh cache to reflect roster change
                await TeamPlayerCache.refresh()

                # Log to audit trail
                logger.info(
                    f"🔧 ADMIN FORCE PICK: Team {team['code']} "
                    f"(Pick #{pick.get('overall')}) → {player['display_name']} "
                    f"by {interaction.user.name}#{interaction.user.discriminator}"
                )

                # Success message
                await interaction.followup.send(
                    f"✅ **Admin Force Pick Confirmed**\n"
                    f"Season: **{season['name']}**\n"
                    f"Team: **{team['code']}**\n"
                    f"Pick: **Round {pick.get('round')}, Overall {pick.get('overall')}**\n"
                    f"Player: **{player['display_name']}**\n\n"
                    f"⚠️ Assigned by {interaction.user.mention}",
                    ephemeral=True,
                )

            except Exception as e:
                logger.error(f"Failed to update pick: {e}")
                await interaction.followup.send(
                    "❌ Error recording pick. Check server logs.",
                    ephemeral=True,
                )
                return

        except Exception as e:
            logger.error(f"Unexpected error in admin_force_pick: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ Unexpected error occurred. Check server logs.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading."""
    await bot.add_cog(TeamDraftPicksCog(bot))
    logger.info("✅ TeamDraftPicksCog loaded")

