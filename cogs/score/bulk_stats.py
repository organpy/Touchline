"""
Bulk Stats Entry - Single modal with multi-line text input for all player stats.
Format: PlayerName,Goals,OwnGoals,Points,Assists,Saves,Blocks,PowerupPlays,PowerupsUsed
One player per line, separated by newlines.

Flow uses buttons between modals since Discord doesn't allow modal->modal responses.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass

from db import get_supabase, queries
from .models import MatchEntry, TeamMatchEntry, PlayerStatEntry
from .roster_cache import RosterCache
from utils.team_player_cache import TeamPlayerCache
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


@dataclass
class ParsedPlayerStat:
    """Parsed player stat from text input"""
    player_name: str
    goals: int = 0
    own_goals: int = 0
    points: int = 0
    assists: int = 0
    saves: int = 0
    blocks: int = 0
    powerup_plays: int = 0
    powerups_used: int = 0


def parse_stats_text(text: str) -> Tuple[List[ParsedPlayerStat], List[str]]:
    """
    Parse multi-line stats text.
    Format: PlayerName,Goals,OwnGoals,Points,Assists,Saves,Blocks,PowerupPlays,PowerupsUsed
    
    Returns: (parsed_stats, errors)
    """
    parsed = []
    errors = []
    
    # Split by newline, handle both /n and actual newlines
    lines = text.replace('/n', '\n').replace('\\n', '\n').strip().split('\n')
    
    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        
        # Split by comma
        parts = [p.strip() for p in line.split(',')]
        
        if len(parts) < 2:
            errors.append(f"Line {line_num}: Not enough values (need at least name and one stat)")
            continue
        
        player_name = parts[0]
        if not player_name:
            errors.append(f"Line {line_num}: Empty player name")
            continue
        
        try:
            # Parse stats with defaults for missing values
            stat = ParsedPlayerStat(
                player_name=player_name,
                goals=int(parts[1]) if len(parts) > 1 and parts[1] else 0,
                own_goals=int(parts[2]) if len(parts) > 2 and parts[2] else 0,
                points=int(parts[3]) if len(parts) > 3 and parts[3] else 0,
                assists=int(parts[4]) if len(parts) > 4 and parts[4] else 0,
                saves=int(parts[5]) if len(parts) > 5 and parts[5] else 0,
                blocks=int(parts[6]) if len(parts) > 6 and parts[6] else 0,
                powerup_plays=int(parts[7]) if len(parts) > 7 and parts[7] else 0,
                powerups_used=int(parts[8]) if len(parts) > 8 and parts[8] else 0,
            )
            parsed.append(stat)
        except ValueError as e:
            errors.append(f"Line {line_num} ({player_name}): Invalid number format")
    
    return parsed, errors


def validate_players_exist(
    parsed_stats: List[ParsedPlayerStat],
    team_code: str,
    roster_cache: RosterCache
) -> Tuple[List[Tuple[ParsedPlayerStat, str]], List[str]]:
    """
    Validate that all players exist on the team roster.
    
    Returns: (valid_players with player_id, invalid_player_names)
    """
    valid = []
    invalid = []
    
    team_players = roster_cache.get_team_players(team_code)
    player_lookup = {p.display_name.lower(): p for p in team_players}
    
    for stat in parsed_stats:
        # Try exact match first, then case-insensitive
        player = player_lookup.get(stat.player_name.lower())
        
        if player:
            valid.append((stat, player.player_id))
        else:
            invalid.append(stat.player_name)
    
    return valid, invalid


class BulkStatsModal(discord.ui.Modal):
    """Modal for entering all player stats in bulk text format."""
    
    def __init__(
        self,
        team_name: str,
        team_code: str,
        team_num: int,
        cog_ref,  # Reference to cog for accessing entry state
        user_id: int,
        roster_cache: RosterCache
    ):
        super().__init__(title=f"{team_name} Stats ({team_num}/2)")
        self.team_code = team_code
        self.team_num = team_num
        self.cog_ref = cog_ref
        self.user_id = user_id
        self.roster_cache = roster_cache
        
        self.stats_input = discord.ui.TextInput(
            label="Player Stats (one per line)",
            placeholder="PlayerName,Goals,OG,Points,Assists,Saves,Blocks,PP,PU\nPlayer2,0,0,5,2,1,0,3,2",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )
        self.add_item(self.stats_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        text = self.stats_input.value
        
        # Parse the text
        parsed_stats, parse_errors = parse_stats_text(text)
        
        if parse_errors:
            error_msg = "**Parse Errors:**\n" + "\n".join(f"• {e}" for e in parse_errors[:10])
            if len(parse_errors) > 10:
                error_msg += f"\n... and {len(parse_errors) - 10} more errors"
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return
        
        if not parsed_stats:
            await interaction.response.send_message("❌ No valid player stats found.", ephemeral=True)
            return
        
        # Validate players exist
        valid_players, invalid_players = validate_players_exist(
            parsed_stats, self.team_code, self.roster_cache
        )
        
        if invalid_players:
            error_msg = "**Players not found on roster:**\n" + "\n".join(f"• {p}" for p in invalid_players)
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return
        
        # Convert to PlayerStatEntry objects
        player_entries = []
        for stat, player_id in valid_players:
            entry = PlayerStatEntry(
                player_id=player_id,
                player_name=stat.player_name,
                goals=stat.goals,
                own_goals=stat.own_goals,
                points=stat.points,
                assists=stat.assists,
                saves=stat.saves,
                blocks=stat.blocks,
                powerup_plays=stat.powerup_plays,
                powerups_used=stat.powerups_used
            )
            player_entries.append(entry)
        
        # Store stats and show next step
        entry = self.cog_ref.active_entries.get(self.user_id)
        if not entry:
            await interaction.response.send_message("❌ Session expired.", ephemeral=True)
            return
        
        if self.team_num == 1:
            entry['team1_stats'] = player_entries
            # Show button to enter Team 2 stats
            await self.cog_ref.show_team2_stats_prompt(interaction, self.user_id)
        else:
            entry['team2_stats'] = player_entries
            # Show confirmation
            await self.cog_ref.show_confirmation(interaction, self.user_id)


class BulkScoreModal(discord.ui.Modal):
    """Modal for entering team scores - for /report-stats-bulk flow."""
    
    def __init__(self, team1_name: str, team2_name: str, cog_ref, user_id: int):
        super().__init__(title="Enter Match Scores")
        self.cog_ref = cog_ref
        self.user_id = user_id
        self.team1_name = team1_name
        self.team2_name = team2_name
        
        self.team1_score = discord.ui.TextInput(
            label=f"{team1_name} Score",
            placeholder="e.g., 45",
            min_length=1,
            max_length=3
        )
        self.team2_score = discord.ui.TextInput(
            label=f"{team2_name} Score",
            placeholder="e.g., 38",
            min_length=1,
            max_length=3
        )
        
        self.add_item(self.team1_score)
        self.add_item(self.team2_score)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            score1 = int(self.team1_score.value.strip())
            score2 = int(self.team2_score.value.strip())
            
            if score1 == score2:
                await interaction.response.send_message("❌ Scores cannot be equal (no draws).", ephemeral=True)
                return
            
            if score1 < 0 or score2 < 0:
                await interaction.response.send_message("❌ Scores cannot be negative.", ephemeral=True)
                return
            
            entry = self.cog_ref.active_entries.get(self.user_id)
            if not entry:
                await interaction.response.send_message("❌ Session expired.", ephemeral=True)
                return
            
            entry['team1_score'] = score1
            entry['team2_score'] = score2
            
            # Show button to enter Team 1 stats (can't chain modals)
            await self.cog_ref.show_team1_stats_prompt(interaction, self.user_id)
            
        except ValueError:
            await interaction.response.send_message("❌ Invalid score format (must be numbers).", ephemeral=True)


class ScoreOnlyModal(discord.ui.Modal):
    """Modal for entering just scores - for /report-score flow."""
    
    def __init__(self, team1_name: str, team2_name: str, cog_ref, user_id: int):
        super().__init__(title="Enter Match Score")
        self.cog_ref = cog_ref
        self.user_id = user_id
        
        self.team1_score = discord.ui.TextInput(
            label=f"{team1_name} Score",
            placeholder="e.g., 45",
            min_length=1,
            max_length=3
        )
        self.team2_score = discord.ui.TextInput(
            label=f"{team2_name} Score",
            placeholder="e.g., 38",
            min_length=1,
            max_length=3
        )
        
        self.add_item(self.team1_score)
        self.add_item(self.team2_score)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            score1 = int(self.team1_score.value.strip())
            score2 = int(self.team2_score.value.strip())
            
            if score1 == score2:
                await interaction.response.send_message("❌ Scores cannot be equal (no draws).", ephemeral=True)
                return
            
            if score1 < 0 or score2 < 0:
                await interaction.response.send_message("❌ Scores cannot be negative.", ephemeral=True)
                return
            
            # Save directly (score-only flow)
            await self.cog_ref.save_score_only(interaction, self.user_id, score1, score2)
            
        except ValueError:
            await interaction.response.send_message("❌ Invalid score format (must be numbers).", ephemeral=True)


class BulkTeamSelectView(discord.ui.View):
    """View for selecting two teams."""
    
    def __init__(self, roster_cache: RosterCache, on_teams_selected):
        super().__init__(timeout=300)
        self.roster_cache = roster_cache
        self.on_teams_selected = on_teams_selected
        self.team1_code = None
        self.team2_code = None
        
        teams = roster_cache.get_all_teams()
        team_options = [
            discord.SelectOption(label=t.name, value=t.code, emoji=t.emoji if t.emoji else None)
            for t in teams
        ][:25]
        
        self.team1_select = discord.ui.Select(
            placeholder="Select Team 1",
            options=team_options,
            row=0
        )
        self.team1_select.callback = self.on_team1
        self.add_item(self.team1_select)
        
        self.team2_select = discord.ui.Select(
            placeholder="Select Team 2",
            options=team_options,
            row=1
        )
        self.team2_select.callback = self.on_team2
        self.add_item(self.team2_select)
    
    async def on_team1(self, interaction: discord.Interaction):
        self.team1_code = interaction.data['values'][0]
        await interaction.response.defer()
    
    async def on_team2(self, interaction: discord.Interaction):
        self.team2_code = interaction.data['values'][0]
        
        if not self.team1_code:
            await interaction.response.send_message("❌ Select Team 1 first.", ephemeral=True)
            return
        
        if self.team1_code == self.team2_code:
            await interaction.response.send_message("❌ Teams must be different.", ephemeral=True)
            self.team2_code = None
            return
        
        # Validate same league
        if not self.roster_cache.teams_in_same_league(self.team1_code, self.team2_code):
            await interaction.response.send_message("❌ Teams must be in the same league.", ephemeral=True)
            self.team2_code = None
            return
        
        await self.on_teams_selected(self.team1_code, self.team2_code, interaction)


class BulkStatsCog(commands.Cog):
    """Bulk stats entry with single multi-line modal."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase = get_supabase()
        self.roster_cache = RosterCache()
        self.active_entries: Dict[int, Dict[str, Any]] = {}  # user_id -> entry state
    
    @app_commands.command(
        name="report-stats-bulk",
        description="[MEMBERS] Report match with bulk player stats (single text input)"
    )
    async def report_stats_bulk(self, interaction: discord.Interaction):
        """Start bulk stats entry flow."""
        # Auto-reload cache if it was invalidated (e.g., after a transaction)
        if not self.roster_cache.loaded:
            logger.info("📦 Roster cache not loaded, reloading for bulk stats...")
            await self.roster_cache.load_cache(self.supabase)
            
        if not self.roster_cache.loaded:
            await interaction.response.send_message(
                "❌ Failed to load roster data. Please try again or contact an admin.",
                ephemeral=True
            )
            return
        
        async def on_teams_selected(team1_code: str, team2_code: str, teams_interaction: discord.Interaction):
            team1 = self.roster_cache.get_team_by_code(team1_code)
            team2 = self.roster_cache.get_team_by_code(team2_code)
            
            if not team1 or not team2:
                await teams_interaction.response.send_message("❌ Team not found.", ephemeral=True)
                return
            
            # Store entry state
            self.active_entries[interaction.user.id] = {
                'team1': team1,
                'team2': team2,
                'team1_stats': [],
                'team2_stats': [],
                'team1_score': 0,
                'team2_score': 0,
            }
            
            # Show match history before entering scores
            # Resolve active season for division
            season = await queries.get_active_season(self.supabase, division_id=team1.division_id)
            if not season:
                await teams_interaction.response.send_message("❌ No active season for this division.", ephemeral=True)
                return

            matches = await queries.get_matches_between_teams(self.supabase, season['id'], team1.team_id, team2.team_id)
            schedules = await queries.get_schedules_between_teams(self.supabase, season['id'], team1.team_id, team2.team_id)

            played = len(matches)
            scheduled = len(schedules)

            # Get configured games limit for this division
            division = await queries.get_division_by_id(self.supabase, team1.division_id)
            league_type = (division.get('league_type') or '').upper() if division else ''
            config_mgr = get_config_manager()
            games_limit = await config_mgr.get_division_games_limit(teams_interaction.guild_id, league_type)
            
            # Use configured limit if schedules not set, otherwise use scheduled count
            max_games = scheduled if scheduled > 0 else games_limit

            desc_lines = [
                f"**{team1.name}** vs **{team2.name}**",
            ]
            if max_games:
                desc_lines.append(f"Limit: **{max_games}** • Played: **{played}**")
            else:
                desc_lines.append(f"Scheduled: **{scheduled}** • Played: **{played}**")
            if matches:
                for m in matches[:10]:
                    t1 = m.get('team1_score', 0)
                    t2 = m.get('team2_score', 0)
                    desc_lines.append(f"• {t1} - {t2} ({m.get('status','final')})")

            embed = discord.Embed(
                title="📜 Head-to-Head",
                description="\n".join(desc_lines),
                color=discord.Color.blurple()
            )

            view = discord.ui.View(timeout=180)

            async def proceed(btn_interaction: discord.Interaction):
                # Block if at or over the games limit
                effective_limit = max_games
                if effective_limit and played >= effective_limit:
                    await btn_interaction.response.send_message(
                        f"⚠️ All {effective_limit} games between these teams are already recorded.",
                        ephemeral=True
                    )
                    return
                score_modal = BulkScoreModal(team1.name, team2.name, self, interaction.user.id)
                await btn_interaction.response.send_modal(score_modal)

            proceed_btn = discord.ui.Button(label="Continue to Enter Score", style=discord.ButtonStyle.primary)
            proceed_btn.callback = proceed
            view.add_item(proceed_btn)

            await teams_interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        view = BulkTeamSelectView(self.roster_cache, on_teams_selected)
        embed = discord.Embed(
            title="🏆 Bulk Stats Entry",
            description="Select the two teams playing in this match.\n\n"
                        "After selecting teams and scores, you'll enter all player stats "
                        "in a single text box using this format:\n"
                        "```\nPlayerName,Goals,OG,Points,Assists,Saves,Blocks,PP,PU\n```",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def show_team1_stats_prompt(self, interaction: discord.Interaction, user_id: int):
        """Show button to enter Team 1 stats (can't chain modals directly)."""
        entry = self.active_entries.get(user_id)
        if not entry:
            await interaction.response.send_message("❌ Session expired.", ephemeral=True)
            return
        
        team1 = entry['team1']
        
        embed = discord.Embed(
            title=f"📊 Enter {team1.name} Stats",
            description=f"Score recorded: **{entry['team1_score']}** - **{entry['team2_score']}**\n\n"
                        f"Click the button below to enter player stats for **{team1.name}**.",
            color=discord.Color.blue()
        )
        
        view = discord.ui.View(timeout=300)
        
        async def open_team1_modal(btn_interaction: discord.Interaction):
            modal = BulkStatsModal(
                team1.name, team1.code, 1, self, user_id, self.roster_cache
            )
            await btn_interaction.response.send_modal(modal)
        
        btn = discord.ui.Button(label=f"Enter {team1.name} Stats", style=discord.ButtonStyle.primary, emoji="📝")
        btn.callback = open_team1_modal
        view.add_item(btn)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def show_team2_stats_prompt(self, interaction: discord.Interaction, user_id: int):
        """Show button to enter Team 2 stats."""
        entry = self.active_entries.get(user_id)
        if not entry:
            await interaction.response.send_message("❌ Session expired.", ephemeral=True)
            return
        
        team1 = entry['team1']
        team2 = entry['team2']
        
        embed = discord.Embed(
            title=f"✅ {team1.name} Stats Saved",
            description=f"**{len(entry['team1_stats'])}** players recorded.\n\n"
                        f"Click the button below to enter stats for **{team2.name}**.",
            color=discord.Color.blue()
        )
        
        view = discord.ui.View(timeout=300)
        
        async def open_team2_modal(btn_interaction: discord.Interaction):
            modal = BulkStatsModal(
                team2.name, team2.code, 2, self, user_id, self.roster_cache
            )
            await btn_interaction.response.send_modal(modal)
        
        btn = discord.ui.Button(label=f"Enter {team2.name} Stats", style=discord.ButtonStyle.primary, emoji="📝")
        btn.callback = open_team2_modal
        view.add_item(btn)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def show_confirmation(self, interaction: discord.Interaction, user_id: int):
        """Show confirmation embed before saving."""
        entry = self.active_entries.get(user_id)
        if not entry:
            await interaction.response.send_message("❌ Session expired.", ephemeral=True)
            return
        
        team1 = entry['team1']
        team2 = entry['team2']
        
        embed = discord.Embed(
            title="✅ Confirm Match Data",
            description="Review before saving to database.",
            color=discord.Color.green()
        )
        
        # Team 1 summary
        t1_lines = [f"**{team1.name}** ({entry['team1_score']} pts)"]
        for stat in entry['team1_stats']:
            t1_lines.append(f"• {stat.player_name}: {stat.goals}G {stat.own_goals}OG {stat.points}P {stat.assists}A {stat.saves}S {stat.blocks}B")
        embed.add_field(name="Team 1", value="\n".join(t1_lines) or "No players", inline=False)
        
        # Team 2 summary
        t2_lines = [f"**{team2.name}** ({entry['team2_score']} pts)"]
        for stat in entry['team2_stats']:
            t2_lines.append(f"• {stat.player_name}: {stat.goals}G {stat.own_goals}OG {stat.points}P {stat.assists}A {stat.saves}S {stat.blocks}B")
        embed.add_field(name="Team 2", value="\n".join(t2_lines) or "No players", inline=False)
        
        view = discord.ui.View(timeout=120)
        
        async def on_confirm(btn_interaction: discord.Interaction):
            await btn_interaction.response.defer()
            await self.save_match(btn_interaction, user_id)
        
        async def on_cancel(btn_interaction: discord.Interaction):
            if user_id in self.active_entries:
                del self.active_entries[user_id]
            await btn_interaction.response.send_message("❌ Cancelled.", ephemeral=True)
        
        confirm_btn = discord.ui.Button(label="✅ Save Match", style=discord.ButtonStyle.success)
        confirm_btn.callback = on_confirm
        view.add_item(confirm_btn)
        
        cancel_btn = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.danger)
        cancel_btn.callback = on_cancel
        view.add_item(cancel_btn)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def save_match(self, interaction: discord.Interaction, user_id: int):
        """Save match and stats to database."""
        entry = self.active_entries.get(user_id)
        if not entry:
            await interaction.followup.send("❌ Session expired.", ephemeral=True)
            return
        
        try:
            team1 = entry['team1']
            team2 = entry['team2']
            
            # Verify teams are in same division
            if not team1.division_id or not team2.division_id or team1.division_id != team2.division_id:
                await interaction.followup.send("❌ Teams must be in the same division.", ephemeral=True)
                return
            
            # Get active season for this division
            season = await queries.get_active_season(self.supabase, division_id=team1.division_id)
            if not season:
                await interaction.followup.send("❌ No active season found for this division.", ephemeral=True)
                return
            
            # Build match entry
            match_entry = MatchEntry(
                team1=TeamMatchEntry(
                    team_id=team1.team_id,
                    team_code=team1.code,
                    team_name=team1.name,
                    emoji=team1.emoji,
                    score=entry['team1_score']
                ),
                team2=TeamMatchEntry(
                    team_id=team2.team_id,
                    team_code=team2.code,
                    team_name=team2.name,
                    emoji=team2.emoji,
                    score=entry['team2_score']
                )
            )
            
            # Add player stats
            for stat in entry['team1_stats']:
                match_entry.team1.add_player_stats(stat)
            for stat in entry['team2_stats']:
                match_entry.team2.add_player_stats(stat)
            
            # Insert match - use the team's division_id
            match_data = match_entry.to_supabase_match(season['id'], team1.division_id)
            match_response = self.supabase.table('matches').insert(match_data).execute()
            
            if not match_response.data:
                await interaction.followup.send("❌ Failed to insert match.", ephemeral=True)
                return
            
            match_id = match_response.data[0]['id']
            
            # Insert player stats
            player_stats = match_entry.to_supabase_player_stats(match_id)
            if player_stats:
                stats_response = self.supabase.table('player_stats').insert(player_stats).execute()
                if not stats_response.data:
                    await interaction.followup.send("❌ Failed to insert player stats.", ephemeral=True)
                    return
            
            # Success
            embed = discord.Embed(
                title="✅ Match Saved!",
                description=f"Match ID: `{match_id}`",
                color=discord.Color.green()
            )
            embed.add_field(
                name=f"{team1.name}",
                value=f"Score: {entry['team1_score']}\nPlayers: {len(entry['team1_stats'])}",
                inline=True
            )
            embed.add_field(
                name=f"{team2.name}",
                value=f"Score: {entry['team2_score']}\nPlayers: {len(entry['team2_stats'])}",
                inline=True
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Cleanup
            if user_id in self.active_entries:
                del self.active_entries[user_id]
            
            logger.info(f"✅ Bulk match saved: {team1.code} vs {team2.code} (match_id={match_id})")
        
        except Exception as e:
            logger.error(f"Error saving bulk match: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(
        name="report-stats-help",
        description="[STATS] Show format guide for bulk stats entry"
    )
    async def report_stats_help(self, interaction: discord.Interaction):
        """Show help for stats format."""
        embed = discord.Embed(
            title="📊 Stats Entry Format Guide",
            description="How to format player stats for bulk entry.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Format",
            value="```\nPlayerName,Goals,OG,Points,Assists,Saves,Blocks,PP,PU\n```",
            inline=False
        )
        
        embed.add_field(
            name="Columns",
            value=(
                "1. **PlayerName** - Exact name on roster\n"
                "2. **Goals** - Goals scored\n"
                "3. **OG** - Own goals\n"
                "4. **Points** - Total points\n"
                "5. **Assists** - Assists\n"
                "6. **Saves** - Saves\n"
                "7. **Blocks** - Blocks\n"
                "8. **PP** - Powerup plays\n"
                "9. **PU** - Powerups used"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Example",
            value="```\nOrgan,0,0,56,9,4,7,5,8\nHakirara,6,0,47,6,7,6,2,4\nAce,2,1,32,4,2,3,1,1\n```",
            inline=False
        )
        
        embed.add_field(
            name="📋 Google Sheets Setup",
            value=(
                "1. Create columns: Name, G, OG, P, A, S, B, PP, PU\n"
                "2. Enter stats in each row\n"
                "3. In a new column, use formula:\n"
                "   `=A2&\",\"&B2&\",\"&C2&\",\"&D2&\",\"&E2&\",\"&F2&\",\"&G2&\",\"&H2&\",\"&I2`\n"
                "4. Drag formula down for all players\n"
                "5. Copy the formula column and paste into the modal"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Tips",
            value=(
                "• One player per line\n"
                "• Player names must match roster exactly\n"
                "• Use `/n` or actual newlines between players\n"
                "• Missing values default to 0"
            ),
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(
        name="report-score",
        description="[MEMBERS] Report match score only (no player stats)"
    )
    async def report_score(self, interaction: discord.Interaction):
        """Report just the match score without player stats."""
        # Auto-reload cache if it was invalidated (e.g., after a transaction)
        if not self.roster_cache.loaded:
            logger.info("📦 Roster cache not loaded, reloading for score report...")
            await self.roster_cache.load_cache(self.supabase)
            
        if not self.roster_cache.loaded:
            await interaction.response.send_message(
                "❌ Failed to load roster data. Please try again or contact an admin.",
                ephemeral=True
            )
            return
        
        async def on_teams_selected(team1_code: str, team2_code: str, teams_interaction: discord.Interaction):
            team1 = self.roster_cache.get_team_by_code(team1_code)
            team2 = self.roster_cache.get_team_by_code(team2_code)
            
            if not team1 or not team2:
                await teams_interaction.response.send_message("❌ Team not found.", ephemeral=True)
                return
            
            # Store entry state for score-only
            self.active_entries[interaction.user.id] = {
                'team1': team1,
                'team2': team2,
                'score_only': True,
            }
            
            # Show head-to-head preview and block if all scheduled played
            season = await queries.get_active_season(self.supabase, division_id=team1.division_id)
            if not season:
                await teams_interaction.response.send_message("❌ No active season for this division.", ephemeral=True)
                return

            matches = await queries.get_matches_between_teams(self.supabase, season['id'], team1.team_id, team2.team_id)
            schedules = await queries.get_schedules_between_teams(self.supabase, season['id'], team1.team_id, team2.team_id)
            played = len(matches)
            scheduled = len(schedules)

            # Get configured games limit for this division
            division = await queries.get_division_by_id(self.supabase, team1.division_id)
            league_type = (division.get('league_type') or '').upper() if division else ''
            config_mgr = get_config_manager()
            games_limit = await config_mgr.get_division_games_limit(teams_interaction.guild_id, league_type)
            
            # Use configured limit if schedules not set, otherwise use scheduled count
            max_games = scheduled if scheduled > 0 else games_limit

            lines = [
                f"**{team1.name}** vs **{team2.name}**",
            ]
            if max_games:
                lines.append(f"Limit: **{max_games}** • Played: **{played}**")
            else:
                lines.append(f"Scheduled: **{scheduled}** • Played: **{played}**")
            for m in matches[:10]:
                t1 = m.get('team1_score', 0)
                t2 = m.get('team2_score', 0)
                lines.append(f"• {t1} - {t2} ({m.get('status','final')})")

            embed = discord.Embed(title="📜 Head-to-Head", description="\n".join(lines), color=discord.Color.blurple())
            view = discord.ui.View(timeout=180)

            async def proceed(btn_interaction: discord.Interaction):
                # Block if at or over the games limit
                effective_limit = max_games
                if effective_limit and played >= effective_limit:
                    await btn_interaction.response.send_message(
                        f"⚠️ All {effective_limit} games between these teams are already recorded.",
                        ephemeral=True
                    )
                    return
                score_modal = ScoreOnlyModal(team1.name, team2.name, self, interaction.user.id)
                await btn_interaction.response.send_modal(score_modal)

            btn = discord.ui.Button(label="Continue to Enter Score", style=discord.ButtonStyle.primary)
            btn.callback = proceed
            view.add_item(btn)

            await teams_interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        view = BulkTeamSelectView(self.roster_cache, on_teams_selected)
        embed = discord.Embed(
            title="🏆 Report Match Score",
            description="Record match result (score only, no player stats).\n\n"
                        "This updates the match table and standings.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def save_score_only(self, interaction: discord.Interaction, user_id: int, score1: int, score2: int):
        """Save score-only match to database."""
        entry = self.active_entries.get(user_id)
        if not entry:
            await interaction.response.send_message("❌ Session expired.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            team1 = entry['team1']
            team2 = entry['team2']
            
            # Verify teams are in same division
            if not team1.division_id or not team2.division_id or team1.division_id != team2.division_id:
                await interaction.followup.send("❌ Teams must be in the same division.", ephemeral=True)
                return
            
            # Get active season for this division
            season = await queries.get_active_season(self.supabase, division_id=team1.division_id)
            if not season:
                await interaction.followup.send("❌ No active season found for this division.", ephemeral=True)
                return
            
            # Build match entry (no player stats)
            match_entry = MatchEntry(
                team1=TeamMatchEntry(
                    team_id=team1.team_id,
                    team_code=team1.code,
                    team_name=team1.name,
                    emoji=team1.emoji,
                    score=score1
                ),
                team2=TeamMatchEntry(
                    team_id=team2.team_id,
                    team_code=team2.code,
                    team_name=team2.name,
                    emoji=team2.emoji,
                    score=score2
                )
            )
            
            # Insert match - use the team's division_id
            match_data = match_entry.to_supabase_match(season['id'], team1.division_id)
            match_response = self.supabase.table('matches').insert(match_data).execute()
            
            if not match_response.data:
                await interaction.followup.send("❌ Failed to insert match.", ephemeral=True)
                return
            
            match_id = match_response.data[0]['id']
            
            # Success
            winner = team1.name if score1 > score2 else team2.name
            embed = discord.Embed(
                title="✅ Score Recorded!",
                description=f"Match ID: `{match_id}`",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Result",
                value=f"**{team1.name}** {score1} - {score2} **{team2.name}**",
                inline=False
            )
            embed.add_field(name="Winner", value=f"🏆 {winner}", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Cleanup
            if user_id in self.active_entries:
                del self.active_entries[user_id]
            
            logger.info(f"✅ Score recorded: {team1.code} {score1}-{score2} {team2.code} (match_id={match_id})")
        
        except Exception as e:
            logger.error(f"Error saving score: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BulkStatsCog(bot))
    logger.info("✅ BulkStatsCog loaded")
