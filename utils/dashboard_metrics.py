"""
Dashboard metrics view for admin dashboard.
Displays league health overview with key statistics.
"""

import discord
import logging
from typing import Optional, Dict, Any
from db import get_supabase, queries
from db.db_utils import execute_async

logger = logging.getLogger(__name__)


class DashboardMetricsView(discord.ui.View):
    """Display league health metrics on the admin dashboard."""
    
    def __init__(self, guild_id: int, bot, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.bot = bot
        self.metrics: Optional[Dict[str, Any]] = None
    
    async def fetch_metrics(self) -> Dict[str, Any]:
        """Fetch current league metrics from database."""
        try:
            supabase = get_supabase()
            
            # Fetch counts
            teams_response = await execute_async(supabase.table('teams').select('id'))
            teams_count = len(teams_response.data) if teams_response.data else 0
            
            players_response = await execute_async(supabase.table('players').select('id'))
            players_count = len(players_response.data) if players_response.data else 0
            
            rosters_response = await execute_async(supabase.table('rosters').select('id').filter('left_at', 'is', 'null'))
            active_rosters_count = len(rosters_response.data) if rosters_response.data else 0
            
            matches_response = await execute_async(supabase.table('matches').select('id').eq('status', 'final'))
            completed_matches_count = len(matches_response.data) if matches_response.data else 0
            
            pending_signings = await execute_async(supabase.table('pending_signings').select('id').eq('status', 'pending'))
            pending_signings_count = len(pending_signings.data) if pending_signings.data else 0

            pending_trades = await execute_async(supabase.table('trade_proposals').select('id').eq('status', 'pending'))
            pending_trades_count = len(pending_trades.data) if pending_trades.data else 0
            
            pending_offers_count = pending_signings_count + pending_trades_count
            
            picks_response = await execute_async(supabase.table('draft_picks').select('id').eq('current_owner_team_id', None))
            unassigned_picks_count = len(picks_response.data) if picks_response.data else 0
            
            # Fetch season info
            divisions_response = await execute_async(supabase.table('divisions').select('*'))
            divisions_count = len(divisions_response.data) if divisions_response.data else 0
            
            active_seasons = []
            if divisions_response.data:
                for division in divisions_response.data:
                    seasons_response = await execute_async(supabase.table('seasons').select('id').eq(
                        'division_id', division['id']
                    ).eq('active', True))
                    if seasons_response.data:
                        active_seasons.append({
                            'name': division.get('name', 'Unknown'),
                            'season_count': len(seasons_response.data)
                        })
            
            self.metrics = {
                'teams': teams_count,
                'players': players_count,
                'active_rosters': active_rosters_count,
                'completed_matches': completed_matches_count,
                'pending_offers': pending_offers_count,
                'unassigned_picks': unassigned_picks_count,
                'divisions': divisions_count,
                'active_seasons': active_seasons,
            }
            
            return self.metrics
        
        except Exception as e:
            logger.error(f"Error fetching dashboard metrics: {e}", exc_info=True)
            return {
                'teams': 0,
                'players': 0,
                'active_rosters': 0,
                'completed_matches': 0,
                'pending_offers': 0,
                'unassigned_picks': 0,
                'divisions': 0,
                'active_seasons': [],
                'error': str(e)
            }
    
    def get_metrics_embed(self) -> discord.Embed:
        """Build metrics embed."""
        if not self.metrics:
            embed = discord.Embed(
                title="📊 League Health Metrics",
                description="Loading metrics...",
                color=discord.Color.blurple()
            )
            return embed
        
        embed = discord.Embed(
            title="📊 League Health Metrics",
            description="Current status of league operations",
            color=discord.Color.green()
        )
        
        # Teams and Players
        embed.add_field(
            name="👥 Teams & Players",
            value=f"**Teams**: {self.metrics['teams']}\n"
                  f"**Players**: {self.metrics['players']}\n"
                  f"**Active Rosters**: {self.metrics['active_rosters']}",
            inline=True
        )
        
        # Matches and Picks
        embed.add_field(
            name="🏀 Matches & Picks",
            value=f"**Completed Matches**: {self.metrics['completed_matches']}\n"
                  f"**Unassigned Picks**: {self.metrics['unassigned_picks']}",
            inline=True
        )
        
        # Transactions
        pending_emoji = "⚠️" if self.metrics['pending_offers'] > 0 else "✅"
        embed.add_field(
            name="📝 Transactions",
            value=f"{pending_emoji} **Pending Offers**: {self.metrics['pending_offers']}",
            inline=False
        )
        
        # Seasons
        season_text = ""
        if self.metrics['active_seasons']:
            for season_info in self.metrics['active_seasons']:
                season_text += f"• **{season_info['name']}**: {season_info['season_count']} active\n"
        else:
            season_text = "No active seasons"
        
        embed.add_field(
            name="📅 Seasons",
            value=f"**Active Divisions**: {self.metrics['divisions']}\n{season_text}",
            inline=False
        )
        
        # Health status
        health_score = self._calculate_health_score()
        embed.add_field(
            name="❤️ League Health",
            value=f"Overall: {health_score}%",
            inline=False
        )
        
        embed.set_footer(text="Metrics refreshed on load")
        
        return embed
    
    def _calculate_health_score(self) -> int:
        """Calculate overall league health score (0-100)."""
        score = 100
        
        # Deduct points for issues
        if self.metrics['pending_offers'] > 5:
            score -= 10
        
        if self.metrics['unassigned_picks'] > 0:
            score -= 5
        
        if self.metrics['completed_matches'] == 0:
            score -= 20  # No games played yet
        
        if self.metrics['teams'] == 0:
            score -= 50  # No teams configured
        
        if self.metrics['active_rosters'] < self.metrics['teams']:
            score -= 10  # Incomplete rosters
        
        return max(0, score)
    
    @discord.ui.button(label="🔄 Refresh Metrics", style=discord.ButtonStyle.secondary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Refresh metrics display."""
        await interaction.response.defer()
        await self.fetch_metrics()
        embed = self.get_metrics_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="⚠️ Show Issues", style=discord.ButtonStyle.secondary)
    async def issues_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show potential issues."""
        issues = []
        
        if not self.metrics:
            await interaction.response.send_message("No metrics available", ephemeral=True)
            return
        
        if self.metrics['teams'] == 0:
            issues.append("🚨 **No teams configured** - Set up teams first")
        
        if self.metrics['players'] == 0:
            issues.append("🚨 **No players registered** - Register players first")
        
        if self.metrics['pending_offers'] > 5:
            issues.append(f"⚠️ **{self.metrics['pending_offers']} pending offers** - Review and approve/deny")
        
        if self.metrics['unassigned_picks'] > 0:
            issues.append(f"⚠️ **{self.metrics['unassigned_picks']} unassigned picks** - Assign draft picks to teams")
        
        if self.metrics['active_rosters'] < self.metrics['teams'] and self.metrics['teams'] > 0:
            issues.append(f"ℹ️ **{self.metrics['teams'] - self.metrics['active_rosters']} teams need rosters** - Add players to rosters")
        
        if not issues:
            issues.append("✅ **No issues detected** - League is healthy!")
        
        embed = discord.Embed(
            title="⚠️ League Issues",
            description="\n".join(issues),
            color=discord.Color.orange()
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
