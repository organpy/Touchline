"""Team owner command to select/make draft picks."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional, Any, Dict, List
from datetime import datetime
import time

from db import get_supabase, queries
from db.transactions_repository import TransactionsRepository
from utils.team_player_cache import TeamPlayerCache
from utils.config_manager import get_config_manager
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    build_team_logo_payload,
    target_thumbnail,
)
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)

# TTL cache for autocomplete to reduce DB queries (30 second TTL)
_autocomplete_cache: Dict[int, Dict[str, Any]] = {}  # user_id -> {data, timestamp}
_AUTOCOMPLETE_TTL = 30  # seconds


class TeamDraftPicksCog(commands.Cog):
    """Cog for team owners to make draft picks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)

    @staticmethod
    def _format_league_season(season: Optional[Dict[str, Any]]) -> str:
        """Return "League — Season" label for messaging."""
        if not season:
            return "Unknown League — Unknown Season"

        season_name = season.get('name') or "Unknown Season"
        league_name = (season.get('league_name') or '').strip()
        league_code = (season.get('league_type') or '').upper()

        if league_name and league_code and league_name.upper() != league_code:
            league_display = f"{league_name} ({league_code})"
        else:
            league_display = league_name or league_code or "Unknown League"

        return f"{league_display} — {season_name}"

    async def autocomplete_available_players(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for available players (not yet picked). Uses 30s TTL cache.
        Filters to show only players from the user's division.
        """
        try:
            user_id = interaction.user.id
            now = time.time()

            # Get user's team (need division)
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            if not team:
                return []
            
            division_id = team['division_id']
            cache_key = f"{user_id}_{division_id}"

            # Check cache
            cached = _autocomplete_cache.get(cache_key)
            if cached and (now - cached['timestamp']) < _AUTOCOMPLETE_TTL:
                all_players = cached['players']
            else:
                # Get active season for this division
                try:
                    seasons_response = self.supabase.table('seasons').select('id').eq(
                        'division_id', division_id
                    ).eq('active', True).limit(1).execute()
                    if not seasons_response.data:
                        return []
                    season_id = seasons_response.data[0]['id']
                except Exception as e:
                    logger.warning(f"Failed to get active season: {e}")
                    return []

                # Get all teams in this division
                try:
                    teams_response = self.supabase.table('teams').select('id').eq(
                        'division_id', division_id
                    ).execute()
                    division_team_ids = {t['id'] for t in (teams_response.data or [])}
                    if not division_team_ids:
                        return []
                except Exception as e:
                    logger.warning(f"Failed to get division teams: {e}")
                    return []

                # Get all available players from rosters in this season (only division teams)
                try:
                    response = self.supabase.table('rosters').select(
                        'players(id, display_name)'
                    ).eq('season_id', season_id).is_(
                        'left_at', 'null'
                    ).execute()
                    
                    roster_player_ids = set()
                    for roster in (response.data or []):
                        # Check if this roster belongs to a team in this division
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

                # Filter by: on roster + not picked yet
                available_ids = roster_player_ids - picked_ids
                if not available_ids:
                    return []

                # Get full player details for available players
                try:
                    all_players_response = self.supabase.table('players').select(
                        'id, display_name'
                    ).in_('id', list(available_ids)).execute()
                    all_players = all_players_response.data or []
                except Exception as e:
                    logger.warning(f"Failed to get available players: {e}")
                    return []

                # Store in cache
                _autocomplete_cache[cache_key] = {
                    'players': all_players,
                    'timestamp': now
                }

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
                    value=p.get('id', '')  # UUID value
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
            # Check if picks are paused globally
            config_mgr = get_config_manager()
            config = await config_mgr.get_config(interaction.guild_id)
            if config.get('picks_paused', False):
                await interaction.followup.send(
                    "⏸️ Draft picks are currently **paused**. Contact an admin.",
                    ephemeral=True,
                )
                return

            # Get user's team
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            
            if not team:
                await interaction.followup.send(
                    "❌ You don't own any team. Contact an admin.",
                    ephemeral=True,
                )
                return

            # Check if this is NBL division (picks only work for NBL)
            try:
                div_response = self.supabase.table('divisions').select('league_type').eq(
                    'id', team['division_id']
                ).limit(1).execute()
                if div_response.data:
                    league_type = div_response.data[0].get('league_type', '').upper()
                    if league_type != 'NBL':
                        await interaction.followup.send(
                            f"❌ Draft picks are only available for **NBL** teams.",
                            ephemeral=True,
                        )
                        return
            except Exception as e:
                logger.warning(f"Failed to check division type: {e}")

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

            # Validate player_id is a valid UUID (not a player name)
            try:
                import uuid
                uuid.UUID(player_id)  # This will raise ValueError if not a valid UUID
            except (ValueError, AttributeError):
                await interaction.followup.send(
                    "❌ Invalid player selection. Please select from the autocomplete list.",
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
                        f"❌ **{team['code']}** has no available picks remaining.\n\n"
                        f"**Season:** {season['name']}\n"
                        f"All allocated picks have been selected.",
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

            league_code = (season.get('league_type') or '').upper() or None
            builder = EmbedBuilder(
                title=f"📋 {team['code']} Draft Picks",
                description=f"Season: **{season['name']}**",
                team_code=team.get('code'),
                division_code=league_code
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
                    
                    builder.add_field(
                        f"Round {pick.get('round')}, Pick #{pick.get('overall')} ✅",
                        f"Selected: **{player_name}**",
                        inline=False,
                    )
                    completed += 1
                else:
                    builder.add_field(
                        f"Round {pick.get('round')}, Pick #{pick.get('overall')} ⏳",
                        "Not yet selected",
                        inline=False,
                    )
                    remaining += 1

            builder.set_footer_text(f"Remaining: {remaining} | Completed: {completed}")
            if team.get('code'):
                builder.add_team_image(team['code'], position="thumbnail")

            embed, files = builder.build_with_files()
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
            # Resolve team by code FIRST (need division_id for season lookup)
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

            # Get active season FOR THIS TEAM'S DIVISION (critical fix)
            try:
                seasons_response = self.supabase.table('seasons').select('*').eq(
                    'division_id', team['division_id']
                ).eq(
                    'active', True
                ).limit(1).execute()
                if not seasons_response.data:
                    await interaction.followup.send(
                        f"❌ No active season found for {team['code']}'s division.",
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
                        league_season_display = self._format_league_season(season)
                        await interaction.followup.send(
                            f"❌ Pick #{pick_number} not found in {league_season_display}.",
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
                            f"❌ **{team['code']}** has no available picks remaining.\n\n"
                            f"**Season:** {season['name']}\n"
                            f"All allocated picks have been selected.",
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

                # Refresh cache for affected team
                cache = TeamPlayerCache()
                await cache.invalidate_team(team['id'])

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

