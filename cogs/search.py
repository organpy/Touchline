"""
Search command for finding players and teams.
Fuzzy matching with pagination.
"""

import discord
from discord.ext import commands
import logging
from typing import Optional, List
from difflib import SequenceMatcher
import math

from db import get_supabase, queries
from utils.team_player_cache import TeamPlayerCache
from utils.error_handler import send_error, ErrorCategory

logger = logging.getLogger(__name__)


class SearchResultsView(discord.ui.View):
    """Paginated view for search results."""
    
    def __init__(self, results: List[dict], page_size: int = 10, result_type: str = "players"):
        super().__init__(timeout=180)
        self.results = results
        self.page_size = page_size
        self.result_type = result_type
        self.current_page = 0
        self.total_pages = math.ceil(len(results) / page_size)
        
        self.update_buttons()
    
    def get_page_results(self) -> List[dict]:
        """Get results for current page."""
        start = self.current_page * self.page_size
        end = start + self.page_size
        return self.results[start:end]
    
    def get_embed(self) -> discord.Embed:
        """Generate embed for current page."""
        page_results = self.get_page_results()
        
        title = f"🔍 Search Results: {self.result_type.title()}"
        embed = discord.Embed(title=title, color=discord.Color.blue())
        
        if self.result_type == "players":
            for i, result in enumerate(page_results, 1):
                team_info = ""
                if result.get("team_name"):
                    team_info = f" • **{result['team_name']}**"
                
                embed.add_field(
                    name=f"{i}. {result.get('display_name', 'Unknown')}",
                    value=f"ID: `{result.get('discord_id', 'N/A')}`{team_info}",
                    inline=False
                )
        
        elif self.result_type == "teams":
            for i, result in enumerate(page_results, 1):
                owner_info = ""
                if result.get("owner_name"):
                    owner_info = f" • Owner: **{result['owner_name']}**"
                
                emoji = result.get("emoji", "🏀")
                embed.add_field(
                    name=f"{i}. {emoji} {result.get('name', 'Unknown')}",
                    value=f"Code: `{result.get('code', 'N/A')}`{owner_info}",
                    inline=False
                )
        
        # Add footer with pagination info
        embed.set_footer(
            text=f"Page {self.current_page + 1}/{self.total_pages} • {len(self.results)} total results"
        )
        
        return embed
    
    def update_buttons(self):
        """Update button states based on current page."""
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page."""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()


def fuzzy_match(query: str, target: str, threshold: float = 0.6) -> float:
    """
    Fuzzy match score between query and target.
    Returns score between 0 and 1, where 1 is perfect match.
    """
    query = query.lower().strip()
    target = target.lower().strip()
    
    # Exact match gets highest score
    if query == target:
        return 1.0
    
    # Prefix match gets high score
    if target.startswith(query):
        return 0.9
    
    # Substring match gets medium score
    if query in target:
        return 0.8
    
    # Use SequenceMatcher for fuzzy matching
    ratio = SequenceMatcher(None, query, target).ratio()
    return ratio if ratio >= threshold else 0


async def search_players(query: str, limit: int = 50) -> List[dict]:
    """Search for players by name with fuzzy matching."""
    supabase = get_supabase()
    cache = TeamPlayerCache()
    
    # Get all players from cache
    all_players = cache.get_all_players()
    
    # Score and filter
    scored = []
    for player in all_players:
        score = fuzzy_match(query, player.get("display_name", ""))
        if score > 0:
            scored.append((score, player))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Get top results and fetch team info
    results = []
    for score, player in scored[:limit]:
        player_info = {
            **player,
            "score": score
        }
        
        # Get team if they're on one
        team_ids = cache.get_player_teams(player["id"])
        if team_ids:
            team = cache.get_team_by_id(team_ids[0])
            if team:
                player_info["team_name"] = team.get("name", "Unknown")
                player_info["team_code"] = team.get("code", "???")
        
        results.append(player_info)
    
    return results


async def search_teams(query: str, limit: int = 50) -> List[dict]:
    """Search for teams by name or code with fuzzy matching."""
    cache = TeamPlayerCache()
    
    # Get all teams from cache
    all_teams = cache.get_all_teams()
    
    # Score by both name and code
    scored = []
    for team in all_teams:
        name_score = fuzzy_match(query, team.get("name", ""))
        code_score = fuzzy_match(query, team.get("code", ""))
        best_score = max(name_score, code_score)
        
        if best_score > 0:
            scored.append((best_score, team))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Get top results with owner info
    results = []
    for score, team in scored[:limit]:
        team_info = {**team, "score": score}
        
        # Get owner name if available
        owner_id = team.get("owner_discord_id")
        if owner_id:
            supabase = get_supabase()
            try:
                owner = await queries.get_player_by_discord_id(supabase, owner_id)
                if owner:
                    team_info["owner_name"] = owner.get("display_name", "Unknown")
            except Exception as e:
                logger.error(f"Error fetching owner info: {e}")
        
        results.append(team_info)
    
    return results


class SearchCog(commands.Cog):
    """Search command for finding players and teams."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @discord.app_commands.command(
        name="search",
        description="[SEARCH] Find players or teams by name/code with fuzzy matching"
    )
    @discord.app_commands.describe(
        query="Name or code to search for (player name or team code)",
        search_type="What to search for (players or teams)"
    )
    async def search(
        self,
        interaction: discord.Interaction,
        query: str,
        search_type: str = "players"
    ):
        """Search for players or teams."""
        await interaction.response.defer(ephemeral=True)
        
        # Validate search type
        if search_type.lower() not in ["players", "teams"]:
            await send_error(
                interaction,
                ErrorCategory.INVALID_INPUT,
                field="Search Type",
                reason="Must be 'players' or 'teams'",
                suggestion="Try `/search query:Ace search_type:players`"
            )
            return
        
        # Validate query length
        if len(query.strip()) < 2:
            await send_error(
                interaction,
                ErrorCategory.INVALID_INPUT,
                field="Query",
                reason="Must be at least 2 characters",
            )
            return
        
        try:
            # Perform search
            if search_type.lower() == "players":
                results = await search_players(query)
                result_type = "Players"
            else:
                results = await search_teams(query)
                result_type = "Teams"
            
            # Handle no results
            if not results:
                embed = discord.Embed(
                    title="🔍 No Results",
                    description=f"No {result_type.lower()} found matching **{query}**. Try a different search term.",
                    color=discord.Color.orange()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Show results with pagination
            view = SearchResultsView(results, result_type=result_type)
            embed = view.get_embed()
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Search error: {e}", exc_info=True)
            await send_error(
                interaction,
                ErrorCategory.DATABASE_ERROR,
                operation="search"
            )


async def setup(bot: commands.Bot):
    """Load cog."""
    await bot.add_cog(SearchCog(bot))
