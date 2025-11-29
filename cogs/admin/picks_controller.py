"""Admin picks controller - simple ON/OFF toggle for NBL draft picks."""

import logging
from typing import Any, Optional

import discord
from discord.ext import commands

from db import get_supabase, queries
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class PicksStatusButton(discord.ui.View):
    """Simple button view for NBL picks status."""

    def __init__(self, bot: Any, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id

    async def update_button_state(self) -> None:
        """Update button label and style based on picks_paused state."""
        config_mgr = get_config_manager()
        config = await config_mgr.get_config(self.guild_id)
        picks_paused = config.get("picks_paused", False)

        self.picks_toggle.style = discord.ButtonStyle.danger if picks_paused else discord.ButtonStyle.success
        self.picks_toggle.label = "Picks: OFF" if picks_paused else "Picks: ON"

    @discord.ui.button(label="Picks: ON", style=discord.ButtonStyle.success, row=0)
    async def picks_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Toggle NBL draft picks on/off for active season."""
        await interaction.response.defer(ephemeral=True)

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
            picks_paused = config.get("picks_paused", False)
            new_state = not picks_paused

            await config_mgr.set_config(self.guild_id, "picks_paused", new_state)

            # Update button and respond
            await self.update_button_state()
            status_text = "**OFF** ⏸️" if new_state else "**ON** ▶️"
            status_color = discord.Color.red() if new_state else discord.Color.green()

            embed = discord.Embed(
                title="🎯 NBL Draft Picks",
                description=f"Status: {status_text}",
                color=status_color,
            )
            embed.add_field(name="Season", value=active_season['name'], inline=True)
            embed.add_field(name="State", value="Paused" if new_state else "Active", inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"{'🔴 Picks paused' if new_state else '🟢 Picks resumed'} by {interaction.user.id} for {active_season['name']}")

        except Exception as e:
            logger.error(f"Error toggling picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to toggle picks.", ephemeral=True)


class PicksControllerCog(commands.Cog):
    """Admin cog for controlling NBL draft picks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Called when bot is ready."""
        logger.info("✅ PicksControllerCog ready")


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading."""
    await bot.add_cog(PicksControllerCog(bot))
    logger.info("✅ PicksControllerCog loaded")
