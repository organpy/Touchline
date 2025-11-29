"""
Transaction Settings Management
Allows admins to toggle global transaction types (signings, trades, releases)
and manage per-team transaction bans.
"""

import logging
from typing import Any, Dict, Optional, List

import discord
from discord import ui

from db import get_supabase
from utils.config_manager import get_config_manager
from utils.team_player_cache import TeamPlayerCache
from cogs.admin.common import LazyInitView

logger = logging.getLogger(__name__)

class TransactionSettingsView(LazyInitView):
    """View for managing transaction settings."""

    def __init__(self, bot: Any, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.config_mgr = get_config_manager()
        self.config_data = {}
        self.original_message: Optional[discord.Message] = None

    async def populate(self) -> None:
        """Load current settings."""
        self.config_data = await self.config_mgr.get_config(self.guild_id)
        self.update_buttons()

    def update_buttons(self) -> None:
        """Update button styles based on config."""
        tx_config = self.config_data.get('transaction_rules', {})
        
        # Signings
        signings_enabled = tx_config.get('signings_enabled', True)
        self.toggle_signings.style = discord.ButtonStyle.success if signings_enabled else discord.ButtonStyle.danger
        self.toggle_signings.label = "Signings: ON" if signings_enabled else "Signings: OFF"
        self.toggle_signings.emoji = "✅" if signings_enabled else "🚫"

        # Trades
        trades_enabled = tx_config.get('trades_enabled', True)
        self.toggle_trades.style = discord.ButtonStyle.success if trades_enabled else discord.ButtonStyle.danger
        self.toggle_trades.label = "Trades: ON" if trades_enabled else "Trades: OFF"
        self.toggle_trades.emoji = "✅" if trades_enabled else "🚫"

        # Releases
        releases_enabled = tx_config.get('releases_enabled', True)
        self.toggle_releases.style = discord.ButtonStyle.success if releases_enabled else discord.ButtonStyle.danger
        self.toggle_releases.label = "Releases: ON" if releases_enabled else "Releases: OFF"
        self.toggle_releases.emoji = "✅" if releases_enabled else "🚫"
        
        # Picks
        picks_paused = self.config_data.get('picks_paused', False)
        self.toggle_picks.style = discord.ButtonStyle.danger if picks_paused else discord.ButtonStyle.success
        self.toggle_picks.label = "Picks: OFF" if picks_paused else "Picks: ON"
        self.toggle_picks.emoji = "⏸️" if picks_paused else "▶️"

    def create_embed(self) -> discord.Embed:
        """Build dark minimalistic settings embed."""
        tx_config = self.config_data.get('transaction_rules', {})
        banned_teams = tx_config.get('banned_teams', [])
        picks_paused = self.config_data.get('picks_paused', False)
        
        embed = discord.Embed(
            title="Transaction Settings",
            description="Toggle transaction permissions.",
            color=0x2B2D31  # Dark theme
        )
        
        status_text = (
            f"**Signings:** {'Enabled' if tx_config.get('signings_enabled', True) else 'Disabled'}\n"
            f"**Trades:** {'Enabled' if tx_config.get('trades_enabled', True) else 'Disabled'}\n"
            f"**Releases:** {'Enabled' if tx_config.get('releases_enabled', True) else 'Disabled'}\n"
            f"**Picks:** {'Paused' if picks_paused else 'Active'}"
        )
        embed.add_field(name="Status", value=status_text, inline=False)
        
        if banned_teams:
            cache = TeamPlayerCache()
            team_names = []
            for team_id in banned_teams:
                team = cache.get_team(team_id)
                if team:
                    team_names.append(f"{team['code']}")
                else:
                    team_names.append(f"Unknown")
            
            embed.add_field(name="Banned", value=", ".join(team_names), inline=False)
        else:
            embed.add_field(name="Banned", value="None", inline=False)
            
        return embed

    async def update_display(self, interaction: discord.Interaction) -> None:
        """Update the message."""
        self.update_buttons()
        embed = self.create_embed()
        if interaction.response.is_done():
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def _toggle_setting(self, interaction: discord.Interaction, key: str):
        """Toggle a boolean setting."""
        await interaction.response.defer()
        tx_config = self.config_data.get('transaction_rules', {})
        current = tx_config.get(key, True)
        tx_config[key] = not current
        
        # Save
        self.config_data['transaction_rules'] = tx_config
        await self.config_mgr.set_config(self.guild_id, 'transaction_rules', tx_config)
        
        await self.update_display(interaction)

    @discord.ui.button(label="Signings: ON", style=discord.ButtonStyle.success, row=0)
    async def toggle_signings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_setting(interaction, 'signings_enabled')

    @discord.ui.button(label="Trades: ON", style=discord.ButtonStyle.success, row=0)
    async def toggle_trades(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_setting(interaction, 'trades_enabled')

    @discord.ui.button(label="Releases: ON", style=discord.ButtonStyle.success, row=0)
    async def toggle_releases(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle_setting(interaction, 'releases_enabled')
    
    @discord.ui.button(label="Picks: ON", style=discord.ButtonStyle.success, row=0)
    async def toggle_picks(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Toggle NBL draft picks on/off."""
        await interaction.response.defer()
        
        try:
            config_mgr = get_config_manager()
            supabase = get_supabase()
            if not supabase:
                await interaction.followup.send("❌ Database unavailable.", ephemeral=True)
                return
            
            # Get NBL division and active season
            nbl_div_response = supabase.table('divisions').select('*').eq('name', 'NBL').execute()
            if not nbl_div_response.data:
                await interaction.followup.send("❌ NBL division not found.", ephemeral=True)
                return
            
            nbl_div_id = nbl_div_response.data[0]['id']
            
            # Get active season for NBL
            seasons_response = supabase.table('seasons').select('*').eq('division_id', nbl_div_id).eq('active', True).execute()
            if not seasons_response.data:
                await interaction.followup.send("❌ No active NBL season found.", ephemeral=True)
                return
            
            active_season = seasons_response.data[0]
            
            # Toggle picks state
            config = await config_mgr.get_config(self.guild_id)
            picks_paused = config.get('picks_paused', False)
            new_state = not picks_paused
            
            await config_mgr.set_config(self.guild_id, 'picks_paused', new_state)
            self.config_data['picks_paused'] = new_state
            
            await self.update_display(interaction)
        
        except Exception as e:
            logger.error(f"Error toggling picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to toggle picks.", ephemeral=True)

    @discord.ui.button(label="Manage Bans", style=discord.ButtonStyle.secondary, row=1)
    async def manage_bans(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TeamBanSelectView(self.bot, self.guild_id, self)
        await view.populate()
        await interaction.response.send_message("Select teams to ban/unban from transactions:", view=view, ephemeral=True)



class TeamBanSelectView(LazyInitView):
    """View to select teams to ban."""
    
    def __init__(self, bot: Any, guild_id: int, parent_view: TransactionSettingsView):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.parent_view = parent_view
        self.config_mgr = get_config_manager()
        self.visible_team_ids: List[str] = []

    async def populate(self) -> None:
        cache = TeamPlayerCache()
        teams = cache.get_all_teams()
        
        # Get current bans
        config = await self.config_mgr.get_config(self.guild_id)
        tx_config = config.get('transaction_rules', {})
        banned_ids = set(tx_config.get('banned_teams', []))
        
        # Create select menu
        options = []
        sorted_teams = sorted(teams, key=lambda t: t['name'])[:25]  # Limit to 25 for select menu
        self.visible_team_ids = [team['id'] for team in sorted_teams]

        for team in sorted_teams:
            is_banned = team['id'] in banned_ids
            label = f"{'🚫 ' if is_banned else ''}{team['name']}"
            options.append(discord.SelectOption(
                label=label[:100],
                value=team['id'],
                description=f"Code: {team['code']}",
                default=is_banned
            ))
            
        if not options:
            options.append(discord.SelectOption(label="No teams found", value="none"))

        select = discord.ui.Select(
            placeholder="Select teams to toggle ban status...",
            min_values=0,
            max_values=len(options),
            options=options
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_ids = interaction.data.get('values', [])
        visible_ids = set(self.visible_team_ids)
        selected_visible_ids = {team_id for team_id in selected_ids if team_id in visible_ids}
        
        config = await self.config_mgr.get_config(self.guild_id)
        tx_config = config.get('transaction_rules', {})
        current_bans = set(tx_config.get('banned_teams', []))
        hidden_bans = current_bans - visible_ids
        new_bans = hidden_bans | selected_visible_ids
        tx_config['banned_teams'] = list(new_bans)
        
        await self.config_mgr.set_config(self.guild_id, 'transaction_rules', tx_config)
        
        # Update parent
        await self.parent_view.populate()
        await self.parent_view.update_display(interaction)
        
        await interaction.followup.send("✅ Team bans updated.", ephemeral=True)


async def setup(bot):
    """Setup function for cog loading (required by discord.py)."""
    logger.info("✅ Transaction Settings module loaded (no cog class needed - provides UI views only)")
