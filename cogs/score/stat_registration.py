"""
Score Registration Cog - Main feature for stat entry
Orchestrates the complete flow: team selection -> scores -> player stats -> save
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import sys
import os

# Avoid circular imports by importing only what's needed
from db import get_supabase, queries
from .models import MatchEntry, TeamMatchEntry, PlayerStatEntry
from .views import (
    TeamSelectionView,
    ScoreEntryModal,
    TeamStatsEntryView,
    MatchConfirmationView
)
from .roster_cache import RosterCache

logger = logging.getLogger(__name__)


class StatRegistrationCog(commands.Cog):
    """Cog for registering match statistics"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.roster_cache = RosterCache()
        self.active_matches = {}  # user_id -> MatchEntry (temp storage during flow)

    async def _send_interaction_response(self, interaction: discord.Interaction, **kwargs):
        """Respond to an interaction, falling back to followup if already handled."""

        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    @commands.Cog.listener()
    async def on_ready(self):
        """Load roster cache when bot starts"""
        if not self.roster_cache.loaded:
            logger.info("📦 Loading roster cache...")
            await self.roster_cache.load_cache(self.supabase)
            logger.info("✅ Roster cache ready!")

    @app_commands.command(
        name="report-stats",
        description="[MEMBERS] Start recording a match with individual player statistics"
    )
    async def report_stats(self, interaction: discord.Interaction):
        """Main command to initiate stat registration flow"""

        if not self.roster_cache.loaded:
            await interaction.response.send_message(
                "❌ Bot is still loading roster data. Try again in a moment.",
                ephemeral=True
            )
            return

        # Step 1: Show team selection
        async def on_teams_selected(team1_code: str, team2_code: str, team_select_interaction: discord.Interaction):
            team1_data = self.roster_cache.get_team_by_code(team1_code)
            team2_data = self.roster_cache.get_team_by_code(team2_code)

            if not team1_data or not team2_data:
                await team_select_interaction.response.send_message(
                    "❌ Team data not found",
                    ephemeral=True
                )
                return

            # Step 2: Show score entry modal
            async def on_scores_entered(team1_score: int, team2_score: int, score_interaction: discord.Interaction):
                # Initialize match entry
                match_entry = MatchEntry(
                    team1=TeamMatchEntry(
                        team_id=team1_data.team_id,
                        team_code=team1_data.code,
                        team_name=team1_data.name,
                        emoji=team1_data.emoji,
                        score=team1_score
                    ),
                    team2=TeamMatchEntry(
                        team_id=team2_data.team_id,
                        team_code=team2_data.code,
                        team_name=team2_data.name,
                        emoji=team2_data.emoji,
                        score=team2_score
                    )
                )

                self.active_matches[interaction.user.id] = match_entry

                # Step 3: Show Team 1 stats entry
                await self.show_team_stats_entry(score_interaction, match_entry, 1)

            score_modal = ScoreEntryModal(team1_data.name, team2_data.name, on_scores_entered)
            await team_select_interaction.response.send_modal(score_modal)

        team_selection_view = TeamSelectionView(self.roster_cache, on_teams_selected)
        embed = discord.Embed(
            title="🏆 Start Match Recording",
            description="Select the two teams playing in this match",
            color=discord.Color.blue()
        )

        await interaction.response.send_message(embed=embed, view=team_selection_view, ephemeral=True)

    async def show_team_stats_entry(
        self,
        interaction: discord.Interaction,
        match_entry: MatchEntry,
        team_num: int
    ):
        """Show the persistent stats entry view for a team"""

        team_match_entry = match_entry.team1 if team_num == 1 else match_entry.team2

        async def on_team_complete(stats_interaction: discord.Interaction):
            if team_num == 1:
                # Move to Team 2
                await self.show_team_stats_entry(stats_interaction, match_entry, 2)
            else:
                # Move to confirmation
                await self.show_match_confirmation(stats_interaction, match_entry)

        stats_view = TeamStatsEntryView(team_match_entry, self.roster_cache, on_team_complete)

        embed = discord.Embed(
            title=f"📊 Team {team_num}: {team_match_entry.team_name}",
            description=f"Add players and their stats | Score: **{team_match_entry.score}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Players Added", value="*No players yet*", inline=False)
        await self._send_interaction_response(interaction, embed=embed, view=stats_view, ephemeral=True)

    async def show_match_confirmation(self, interaction: discord.Interaction, match_entry: MatchEntry):
        """Show final confirmation before saving"""

        embed = discord.Embed(
            title="✅ Confirm Match Details",
            description="Review the match data before saving to database",
            color=discord.Color.green()
        )

        team1_str = f"**{match_entry.team1.team_name}** ({match_entry.team1.score} pts)\n"
        team1_str += "\n".join(
            f"  • {stat.player_name}: {stat.goals}G {stat.own_goals}OG {stat.points}P {stat.assists}A {stat.saves}S {stat.blocks}B"
            for stat in match_entry.team1.get_players_list()
        ) or "  (no players)"

        team2_str = f"**{match_entry.team2.team_name}** ({match_entry.team2.score} pts)\n"
        team2_str += "\n".join(
            f"  • {stat.player_name}: {stat.goals}G {stat.own_goals}OG {stat.points}P {stat.assists}A {stat.saves}S {stat.blocks}B"
            for stat in match_entry.team2.get_players_list()
        ) or "  (no players)"

        embed.add_field(name=match_entry.team1.team_name, value=team1_str, inline=False)
        embed.add_field(name=match_entry.team2.team_name, value=team2_str, inline=False)

        async def on_confirm(confirm_interaction: discord.Interaction):
            await self.save_match_to_database(confirm_interaction, match_entry, interaction.user.id)

        async def on_cancel(confirm_interaction: discord.Interaction):
            await confirm_interaction.followup.send("❌ Match recording cancelled.", ephemeral=True)
            if interaction.user.id in self.active_matches:
                del self.active_matches[interaction.user.id]

        confirmation_view = MatchConfirmationView(match_entry, on_confirm, on_cancel)
        await self._send_interaction_response(interaction, embed=embed, view=confirmation_view, ephemeral=True)

    async def save_match_to_database(
        self,
        interaction: discord.Interaction,
        match_entry: MatchEntry,
        user_id: int
    ):
        """Save match + all player_stats to Supabase"""

        try:
            # Get active season & division
            season = await queries.get_active_season(self.supabase)
            if not season:
                await interaction.followup.send("❌ No active season found", ephemeral=True)
                return

            season_id = season['id']
            division_id = season['division_id']

            # Prepare match data
            match_data = match_entry.to_supabase_match(season_id, division_id)

            # Insert match
            logger.info(f"🔄 Inserting match: {match_entry.team1.team_code} vs {match_entry.team2.team_code}")
            match_response = self.supabase.table('matches').insert(match_data).execute()

            if not match_response.data:
                await interaction.followup.send("❌ Failed to insert match", ephemeral=True)
                logger.error("Match insert returned no data")
                return

            match_id = match_response.data[0]['id']
            logger.info(f"✅ Match inserted: {match_id}")

            # Prepare & insert player_stats
            player_stats_list = match_entry.to_supabase_player_stats(match_id)

            if player_stats_list:
                logger.info(f"🔄 Inserting {len(player_stats_list)} player stats...")
                stats_response = self.supabase.table('player_stats').insert(player_stats_list).execute()

                if not stats_response.data:
                    await interaction.followup.send("❌ Failed to insert player stats", ephemeral=True)
                    logger.error("Player stats insert returned no data")
                    return

                logger.info(f"✅ Player stats inserted: {len(stats_response.data)} entries")

            # Success message
            embed = discord.Embed(
                title="✅ Match Saved Successfully!",
                description=f"Match ID: `{match_id}`",
                color=discord.Color.green()
            )
            embed.add_field(
                name=f"{match_entry.team1.team_name} {match_entry.team1.score}",
                value=f"{len(match_entry.team1.players)} players",
                inline=True
            )
            embed.add_field(
                name=f"{match_entry.team2.team_name} {match_entry.team2.score}",
                value=f"{len(match_entry.team2.players)} players",
                inline=True
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            # Clean up
            if user_id in self.active_matches:
                del self.active_matches[user_id]

            logger.info(
                f"✅ Match complete: {match_entry.team1.team_code} ({match_entry.team1.score}) vs "
                f"{match_entry.team2.team_code} ({match_entry.team2.score})"
            )

        except Exception as e:
            logger.error(f"❌ Error saving match: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error saving match: {str(e)}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Setup function for cog loader"""
    await bot.add_cog(StatRegistrationCog(bot))
    logger.info("✅ StatRegistrationCog loaded")
