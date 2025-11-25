"""
Enhanced help command system with category browsing and search.
Provides structured command discovery with pagination and admin-only help.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import List, Optional, Dict, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class CommandCategory(Enum):
    """Formal command categories."""
    STATS = ("📊 Stats", "Statistics, leaderboards, and standings")
    TEAM_OWNER = ("👔 Team Owner", "Player recruitment and roster management")
    MEMBERS = ("👥 Members", "Public league information and commands")
    SEARCH = ("🔍 Search", "Find players and teams")
    TIMER = ("⏱️ Timer", "Match timing and management")
    SCORE = ("🏀 Score", "Recording match results and stats")
    ADMIN = ("⚙️ Admin", "Server and league administration")
    HELP = ("❓ Help", "Help and documentation")
    OTHER = ("📌 Other", "Miscellaneous commands")


class HelpView(discord.ui.View):
    """Main help view with category selection."""
    
    def __init__(self, bot: commands.Bot, categories: Dict, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.categories = categories
        self.current_category: Optional[CommandCategory] = None
        self.current_page = 0
    
    def get_category_embed(self, category: CommandCategory) -> discord.Embed:
        """Get embed for a specific category."""
        if category not in self.categories:
            embed = discord.Embed(
                title="❌ Category Not Found",
                description="This category has no commands.",
                color=discord.Color.red()
            )
            return embed
        
        commands_list = self.categories[category]
        page_size = 8
        total_pages = (len(commands_list) + page_size - 1) // page_size
        
        start_idx = self.current_page * page_size
        end_idx = start_idx + page_size
        page_commands = commands_list[start_idx:end_idx]
        
        category_name, category_desc = category.value
        embed = discord.Embed(
            title=f"{category_name} Commands",
            description=category_desc,
            color=discord.Color.blue()
        )
        
        for cmd_name, cmd_desc in page_commands:
            # Clean description (remove category tag if present)
            clean_desc = cmd_desc
            if clean_desc.startswith('[') and ']' in clean_desc:
                clean_desc = clean_desc[clean_desc.index(']') + 1:].strip()
            
            embed.add_field(
                name=f"`/{cmd_name}`",
                value=clean_desc[:100] + "..." if len(clean_desc) > 100 else clean_desc,
                inline=False
            )
        
        embed.set_footer(
            text=f"Page {self.current_page + 1}/{total_pages if total_pages > 0 else 1} • Use `/help command:<name>` for details"
        )
        
        return embed
    
    async def update_view(self, interaction: discord.Interaction):
        """Update the view with current category and page."""
        if self.current_category is None:
            # Show category selection
            embed = discord.Embed(
                title="📚 NBLBot Command Categories",
                description="Select a category to view commands. Use `/help search:<keyword>` to find specific commands.",
                color=discord.Color.blurple()
            )
            
            for category in CommandCategory:
                if category in self.categories and self.categories[category]:
                    name, desc = category.value
                    count = len(self.categories[category])
                    embed.add_field(
                        name=f"{name}",
                        value=f"{desc}\n└ *{count} command{'s' if count != 1 else ''}*",
                        inline=False
                    )
            
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
        else:
            # Show specific category
            embed = self.get_category_embed(self.current_category)
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
    
    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.primary)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_category = CommandCategory.STATS
        self.current_page = 0
        await self.update_view(interaction)
    
    @discord.ui.button(label="👔 Team Owner", style=discord.ButtonStyle.primary)
    async def owner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_category = CommandCategory.TEAM_OWNER
        self.current_page = 0
        await self.update_view(interaction)
    
    @discord.ui.button(label="👥 Members", style=discord.ButtonStyle.primary)
    async def members_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_category = CommandCategory.MEMBERS
        self.current_page = 0
        await self.update_view(interaction)
    
    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Enter search query (or say 'cancel' to exit):",
            ephemeral=True,
        )
    
    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_category = None
        self.current_page = 0
        await self.update_view(interaction)
    
    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_category:
            self.current_page = max(0, self.current_page - 1)
            await self.update_view(interaction)


class AdminHelpView(discord.ui.View):
    """Admin help view with admin category selection."""
    
    def __init__(self, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.current_section: Optional[str] = None
    
    def get_admin_embed(self) -> discord.Embed:
        """Get main admin help embed."""
        embed = discord.Embed(
            title="⚙️ Admin Command Reference",
            description="Administrative commands for managing leagues, teams, and players.",
            color=discord.Color.blue()
        )
        
        # Quick commands section
        embed.add_field(
            name="⚡ Quick Commands",
            value="`/admin-force-sign` - Force sign a player\n"
                  "`/admin-force-release` - Force release a player\n"
                  "`/admin-force-pick` - Assign a draft pick\n"
                  "`/config-set-channel` - Set offer channel\n"
                  "`/config-set-expiry` - Set offer expiry (minutes)",
            inline=False
        )
        
        # Dashboard section
        embed.add_field(
            name="📊 Dashboard (`/admin` main command)",
            value="**🏢 Team Management** - Add/edit/delete teams, set owners\n"
                  "**👤 Player Management** - Register players, link Discord IDs\n"
                  "**🏀 Roster Management** - Force sign/release/transfer players\n"
                  "**🏆 Draft Picks** - Seed picks, reassign ownership\n"
                  "**📝 Transactions** - Void/edit/resend announcements\n"
                  "**📅 Season Management** - Create/activate seasons\n"
                  "**⏰ Offers** - Manage signing offers\n"
                  "**📊 Audit** - View logs and exports",
            inline=False
        )
        
        # Server config section
        embed.add_field(
            name="🔧 Server Configuration",
            value="`/configure-server` - Interactive server config panel\n"
                  "`/timer-config` - Timer settings\n"
                  "Configure: roles, channels, transaction settings, notifications",
            inline=False
        )
        
        embed.set_footer(text="Use `/admin-help` for detailed workflow guides")
        return embed
    
    @discord.ui.button(label="⚡ Quick Reference", style=discord.ButtonStyle.primary)
    async def quick_ref_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = self.get_admin_embed()
        await interaction.followup.edit_message(interaction.message.id, embed=embed)
    
    @discord.ui.button(label="📊 Dashboard Guide", style=discord.ButtonStyle.primary)
    async def dashboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = discord.Embed(
            title="📊 Admin Dashboard Guide",
            description="The main dashboard (`/admin`) provides unified control over league operations.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🏢 Team Management",
            value="Create teams, assign owners, manage division affiliation\n"
                  "• Add new teams for the league\n"
                  "• Assign Discord user as team owner\n"
                  "• Edit team details (name, code, emoji)",
            inline=False
        )
        
        embed.add_field(
            name="👤 Player Management",
            value="Register players and link Discord accounts\n"
                  "• Register new player with display name\n"
                  "• Link Discord ID to player account\n"
                  "• Update player information",
            inline=False
        )
        
        embed.add_field(
            name="🏀 Roster Management",
            value="Control team rosters directly\n"
                  "• Force sign player to team\n"
                  "• Release player from team\n"
                  "• Transfer player between teams",
            inline=False
        )
        
        embed.set_footer(text="Use buttons to navigate dashboard sections")
        await interaction.followup.edit_message(interaction.message.id, embed=embed)
    
    @discord.ui.button(label="🏆 Workflows", style=discord.ButtonStyle.primary)
    async def workflows_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = discord.Embed(
            title="🔄 Common Admin Workflows",
            description="Step-by-step guides for frequent admin tasks.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="New Season Setup",
            value="1. `/admin` → Season Management → Create Season\n"
                  "2. Enter season name (e.g., 'S11')\n"
                  "3. Mark as active\n"
                  "4. `/admin-seed-picks` to generate draft picks\n"
                  "5. Season ready for play!",
            inline=False
        )
        
        embed.add_field(
            name="Emergency Player Signing",
            value="1. `/admin-force-sign` player:Name team:Code\n"
                  "2. Bypasses confirmation flow\n"
                  "3. Automatically logged in audit trail\n"
                  "4. Roster updated immediately",
            inline=False
        )
        
        embed.add_field(
            name="Fixing Roster Mistakes",
            value="1. `/admin` → Roster Management\n"
                  "2. Select team and player\n"
                  "3. Choose action: Release, Transfer, or Retract\n"
                  "4. Changes apply immediately",
            inline=False
        )
        
        embed.set_footer(text="All admin actions are logged in audit trail")
        await interaction.followup.edit_message(interaction.message.id, embed=embed)


class HelpCog(commands.Cog):
    """Enhanced help system with category browsing."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    def categorize_commands(self) -> Dict[CommandCategory, List[Tuple[str, str]]]:
        """Categorize all commands by their category tag."""
        categories = {cat: [] for cat in CommandCategory}
        
        for cmd in self.bot.tree.walk_commands():
            desc = cmd.description or "No description"
            category = CommandCategory.OTHER
            
            # Extract category from description
            if desc.startswith('[') and ']' in desc:
                tag = desc[1:desc.index(']')].upper()
                
                # Map tags to categories
                tag_map = {
                    "STATS": CommandCategory.STATS,
                    "TEAM OWNER": CommandCategory.TEAM_OWNER,
                    "MEMBERS": CommandCategory.MEMBERS,
                    "SEARCH": CommandCategory.SEARCH,
                    "TIMER": CommandCategory.TIMER,
                    "SCORE": CommandCategory.SCORE,
                    "ADMIN": CommandCategory.ADMIN,
                    "HELP": CommandCategory.HELP,
                }
                
                category = tag_map.get(tag, CommandCategory.OTHER)
            
            categories[category].append((cmd.name, desc))
        
        return categories
    
    async def autocomplete_command(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for command names."""
        current_lower = (current or "").lower()
        choices = []
        
        for cmd in sorted(self.bot.tree.walk_commands(), key=lambda c: c.name):
            if current_lower and current_lower not in cmd.name.lower():
                continue
            
            choices.append(app_commands.Choice(name=f"/{cmd.name}", value=cmd.name))
            
            if len(choices) >= 25:
                break
        
        return choices
    
    @app_commands.command(
        name="help",
        description="📚 Browse commands by category or get details on a specific command"
    )
    @app_commands.describe(
        command="Get detailed help for a specific command",
        category="Browse commands by category (stats, team_owner, members, search)",
        search="Search for commands by keyword"
    )
    @app_commands.autocomplete(command=autocomplete_command)
    async def help(
        self,
        interaction: discord.Interaction,
        command: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ):
        """
        Browse commands by category or search for specific commands.
        """
        await interaction.response.defer(ephemeral=True)
        
        # Specific command help
        if command:
            cmd_name = str(command).strip().lower()
            target = None
            
            for cmd in self.bot.tree.walk_commands():
                if cmd.name.lower() == cmd_name:
                    target = cmd
                    break
            
            if not target:
                embed = discord.Embed(
                    title="❌ Command Not Found",
                    description=f"Could not find command `{cmd_name}`. Use `/help` to see all commands.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Build detailed help
            embed = discord.Embed(
                title=f"📖 /{target.name}",
                description=target.description or "No description available",
                color=discord.Color.green()
            )
            
            if target.options:
                opts = []
                for opt in target.options:
                    opt_type = getattr(opt.type, "name", "string")
                    required = "required" if getattr(opt, "required", False) else "optional"
                    opts.append(f"**{opt.name}** ({opt_type}) — {required}")
                
                embed.add_field(name="Parameters", value="\n".join(opts), inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Search for commands
        if search:
            search_term = search.lower()
            matching = []
            
            for cmd in self.bot.tree.walk_commands():
                name_match = search_term in cmd.name.lower()
                desc_match = search_term in (cmd.description or "").lower()
                
                if name_match or desc_match:
                    matching.append((cmd.name, cmd.description or "No description"))
            
            if not matching:
                embed = discord.Embed(
                    title="🔍 No Matching Commands",
                    description=f"No commands found matching `{search}`",
                    color=discord.Color.orange()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Show search results
            embed = discord.Embed(
                title=f"🔍 Search Results: {search}",
                description=f"Found {len(matching)} matching command(s)",
                color=discord.Color.blue()
            )
            
            for cmd_name, cmd_desc in matching[:10]:
                clean_desc = cmd_desc
                if clean_desc.startswith('[') and ']' in clean_desc:
                    clean_desc = clean_desc[clean_desc.index(']') + 1:].strip()
                
                embed.add_field(
                    name=f"`/{cmd_name}`",
                    value=clean_desc[:80] + "..." if len(clean_desc) > 80 else clean_desc,
                    inline=False
                )
            
            if len(matching) > 10:
                embed.set_footer(text=f"... and {len(matching) - 10} more. Use `/help command:name` for details.")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Category browser (default)
        categories = self.categorize_commands()
        view = HelpView(self.bot, categories)
        embed = discord.Embed(
            title="📚 NBLBot Command Help",
            description="Select a category to browse commands using the buttons below.",
            color=discord.Color.blurple()
        )
        
        for cat in CommandCategory:
            if categories[cat]:
                name, desc = cat.value
                count = len(categories[cat])
                embed.add_field(
                    name=f"{name} ({count})",
                    value=desc,
                    inline=False
                )
        
        embed.set_footer(text="Use `/help command:<name>` or `/help search:<keyword>` for more options")
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    @app_commands.command(
        name="admin-help",
        description="⚙️ Administrative command reference and workflows"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_help(self, interaction: discord.Interaction):
        """Admin help and workflow reference."""
        await interaction.response.defer(ephemeral=True)
        
        view = AdminHelpView()
        embed = view.get_admin_embed()
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
