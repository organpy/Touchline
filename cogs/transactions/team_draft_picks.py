"""Team owner commands to make and view draft picks.

A team takes part in the draft of the division it plays in this season, if that division has
``picks_enabled``. Picks, pick order and the player pool are all scoped to that division.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from cogs.transactions.pick_trading import picks_enabled_for_division
from db import get_supabase, queries
from db.db_utils import execute_async
from db.transactions_repository import TransactionsRepository
from utils.config_manager import get_config_manager
from utils.embed_builder import EmbedBuilder
from utils.league_cache import LeagueCache
from utils.team_player_cache import TeamPlayerCache

logger = logging.getLogger(__name__)

# TTL cache for autocomplete to reduce DB queries (30 second TTL)
_autocomplete_cache: Dict[str, Dict[str, Any]] = {}
_AUTOCOMPLETE_TTL = 30  # seconds


def draft_context(team: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """(season, division, error) for the draft a team takes part in this season."""
    if not team:
        return None, None, "❌ You don't own any team. Contact an admin."
    season = LeagueCache.active_season()
    if not season:
        return None, None, "❌ There is no active season."
    division = LeagueCache.team_division(team.get('id'), season['id'])
    if not division:
        return None, None, f"❌ **{team.get('name')}** isn't in a division this season."
    if not picks_enabled_for_division(division['id']):
        return None, None, f"❌ **{division.get('name')}** doesn't run a draft."
    return season, division, None


class TeamDraftPicksCog(commands.Cog):
    """Cog for team owners to make draft picks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.repo = TransactionsRepository(self.supabase)

    @staticmethod
    def _format_league_season(season: Optional[Dict[str, Any]], division: Optional[Dict[str, Any]] = None) -> str:
        """Return "Division — Season" label for messaging."""
        season_name = (season or {}).get('name') or "Unknown Season"
        division_name = (division or {}).get('name') or "Unknown Division"
        return f"{division_name} — {season_name}"

    async def _picked_player_ids(self, season_id: str, division_id: str) -> set:
        response = await execute_async(
            self.supabase.table('draft_picks').select('selected_player_id')
            .eq('season_id', season_id).eq('division_id', division_id)
            .not_.is_('selected_player_id', 'null')
        )
        return {p['selected_player_id'] for p in (response.data or [])}

    async def _next_pick(self, season_id: str, division_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        response = await execute_async(
            self.supabase.table('draft_picks').select('*')
            .eq('season_id', season_id).eq('division_id', division_id)
            .eq('current_owner_team_id', team_id).is_('selected_player_id', 'null')
            .order('overall', desc=False).limit(1)
        )
        return response.data[0] if response.data else None

    async def autocomplete_available_players(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Players on a roster in the owner's division who haven't been drafted yet (30s cache)."""
        try:
            team = TeamPlayerCache.get_team_by_owner(interaction.user.id)
            season, division, error = draft_context(team)
            if error:
                return []

            cache_key = f"{season['id']}:{division['id']}"
            cached = _autocomplete_cache.get(cache_key)
            now = time.time()
            if cached and (now - cached['timestamp']) < _AUTOCOMPLETE_TTL:
                players = cached['players']
            else:
                team_ids = set(LeagueCache.team_ids_in_division(division['id'], season['id']))
                picked = await self._picked_player_ids(season['id'], division['id'])
                players = [
                    p for p in TeamPlayerCache.get_all_players()
                    if p.get('current_team_id') in team_ids and p.get('id') not in picked
                ]
                _autocomplete_cache[cache_key] = {'players': players, 'timestamp': now}

            needle = current.lower()
            matches = sorted(
                (p for p in players if needle in (p.get('display_name') or '').lower()),
                key=lambda p: (p.get('display_name') or '').lower(),
            )[:25]
            return [app_commands.Choice(name=p.get('display_name', 'Unknown')[:100], value=p['id']) for p in matches]
        except Exception as e:
            logger.error(f"Error in autocomplete_available_players: {e}")
            return []

    @app_commands.command(name="pick", description="[TEAM OWNER] Make your team's next draft pick")
    @app_commands.describe(player_id="Player to pick (search by name)")
    @app_commands.autocomplete(player_id=autocomplete_available_players)
    async def make_pick(self, interaction: discord.Interaction, player_id: str):
        """Team owner command to make a draft pick."""
        await interaction.response.defer(ephemeral=True)
        try:
            config = await get_config_manager().get_config(interaction.guild_id)
            if config.get('picks_paused', False):
                await interaction.followup.send("⏸️ Draft picks are currently **paused**. Contact an admin.", ephemeral=True)
                return

            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            season, division, error = draft_context(team)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return

            try:
                uuid.UUID(player_id)
            except (ValueError, AttributeError):
                await interaction.followup.send(
                    "❌ Invalid player selection. Please select from the autocomplete list.", ephemeral=True,
                )
                return

            pick = await self._next_pick(season['id'], division['id'], team['id'])
            if not pick:
                await interaction.followup.send(
                    f"❌ **{team['code']}** has no available picks remaining in "
                    f"{self._format_league_season(season, division)}.",
                    ephemeral=True,
                )
                return

            if player_id in await self._picked_player_ids(season['id'], division['id']):
                await interaction.followup.send("⚠️ Player has already been picked.", ephemeral=True)
                return

            result = await execute_async(
                self.supabase.table('draft_picks').update({'selected_player_id': player_id}).eq('id', pick['id'])
            )
            if not result.data:
                await interaction.followup.send("❌ Failed to record pick. Try again.", ephemeral=True)
                return
            _autocomplete_cache.pop(f"{season['id']}:{division['id']}", None)

            player = TeamPlayerCache.get_player(player_id) or {}
            await interaction.followup.send(
                f"✅ **Draft Pick Confirmed** (Round {pick.get('round')}, Overall {pick.get('overall')})\n"
                f"Team: **{team['code']}**\n"
                f"Player: **{player.get('display_name', 'Unknown')}**",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Unexpected error in make_pick: {e}", exc_info=True)
            await interaction.followup.send("❌ Unexpected error occurred. Check server logs.", ephemeral=True)

    @app_commands.command(name="picks", description="[TEAM OWNER] View your team's draft picks")
    async def view_picks(self, interaction: discord.Interaction):
        """Team owner command to view their picks."""
        await interaction.response.defer(ephemeral=True)
        try:
            team = await queries.get_team_by_owner(self.supabase, interaction.user.id)
            season, division, error = draft_context(team)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return

            picks = [
                p for p in await self.repo.list_draft_picks(season['id'], division['id'])
                if p.get('current_owner_team_id') == team['id']
            ]
            if not picks:
                await interaction.followup.send(f"❌ No picks found for {team['code']}.", ephemeral=True)
                return

            builder = EmbedBuilder(
                title=f"📋 {team['code']} Draft Picks",
                description=f"**{self._format_league_season(season, division)}**",
                team_code=team.get('code'),
                division_code=division.get('code'),
            )
            remaining = completed = 0
            for pick in picks:
                heading = f"Round {pick.get('round')}, Pick #{pick.get('overall')}"
                if pick.get('selected_player_id'):
                    player = TeamPlayerCache.get_player(pick['selected_player_id']) or {}
                    builder.add_field(f"{heading} ✅", f"Selected: **{player.get('display_name', 'Unknown')}**", inline=False)
                    completed += 1
                else:
                    builder.add_field(f"{heading} ⏳", "Not yet selected", inline=False)
                    remaining += 1

            builder.set_footer_text(f"Remaining: {remaining} | Completed: {completed}")
            if team.get('code'):
                builder.add_team_image(team['code'], position="thumbnail")
            embed, files = builder.build_with_files()
            await interaction.followup.send(embed=embed, ephemeral=True, files=files or None)
        except Exception as e:
            logger.error(f"Error in view_picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Error loading picks.", ephemeral=True)

    @app_commands.command(name="admin-force-pick", description="[ADMIN] Admin: Force assign a draft pick to a player")
    @app_commands.describe(
        team_code="Team code",
        player_name="Player display name",
        pick_number="Overall pick number (optional: auto-select next available)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_force_pick(
        self,
        interaction: discord.Interaction,
        team_code: str,
        player_name: str,
        pick_number: Optional[int] = None,
    ) -> None:
        """Admin can force assign a draft pick to any player without validation."""
        await interaction.response.defer(ephemeral=True)
        try:
            team = await queries.get_team_by_code(self.supabase, team_code)
            if not team:
                await interaction.followup.send(f"❌ Team code `{team_code}` not found.", ephemeral=True)
                return
            season, division, error = draft_context(team)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return

            player = await queries.get_player_by_name(self.supabase, player_name)
            if not player:
                await interaction.followup.send(f"❌ Player `{player_name}` not found.", ephemeral=True)
                return

            if pick_number:
                response = await execute_async(
                    self.supabase.table('draft_picks').select('*')
                    .eq('season_id', season['id']).eq('division_id', division['id'])
                    .eq('overall', pick_number).limit(1)
                )
                pick = response.data[0] if response.data else None
                if not pick:
                    await interaction.followup.send(
                        f"❌ Pick #{pick_number} not found in {self._format_league_season(season, division)}.",
                        ephemeral=True,
                    )
                    return
            else:
                pick = await self._next_pick(season['id'], division['id'], team['id'])
                if not pick:
                    await interaction.followup.send(
                        f"❌ **{team['code']}** has no available picks remaining in {season['name']}.",
                        ephemeral=True,
                    )
                    return

            result = await execute_async(
                self.supabase.table('draft_picks').update({'selected_player_id': player['id']}).eq('id', pick['id'])
            )
            if not result.data:
                await interaction.followup.send("❌ Failed to record pick. Try again.", ephemeral=True)
                return
            _autocomplete_cache.pop(f"{season['id']}:{division['id']}", None)
            await TeamPlayerCache.invalidate_team(team['id'])

            logger.info(
                "🔧 ADMIN FORCE PICK: %s (pick #%s) → %s by %s",
                team['code'], pick.get('overall'), player['display_name'], interaction.user.id,
            )
            await interaction.followup.send(
                f"✅ **Admin Force Pick Confirmed**\n"
                f"Season: **{self._format_league_season(season, division)}**\n"
                f"Team: **{team['code']}**\n"
                f"Pick: **Round {pick.get('round')}, Overall {pick.get('overall')}**\n"
                f"Player: **{player['display_name']}**\n\n"
                f"⚠️ Assigned by {interaction.user.mention}",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Unexpected error in admin_force_pick: {e}", exc_info=True)
            await interaction.followup.send("❌ Unexpected error occurred. Check server logs.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading."""
    await bot.add_cog(TeamDraftPicksCog(bot))
    logger.info("✅ TeamDraftPicksCog loaded")
