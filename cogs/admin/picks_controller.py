"""Admin picks controller - ON/OFF toggle for draft pick trading in every division that runs a draft."""

import logging
from typing import Any, Optional

import discord
from discord.ext import commands

from db import get_supabase
from utils.league_cache import LeagueCache
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class PicksStatusButton(discord.ui.View):
    """Button view for the draft picks pause state."""

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
        """Toggle draft pick trading on/off for the active season."""
        await interaction.response.defer(ephemeral=True)

        try:
            config_mgr = get_config_manager()
            supabase = get_supabase()
            if not supabase:
                await interaction.followup.send("❌ Database unavailable.", ephemeral=True)
                return

            await LeagueCache.ensure_ready()
            active_season = LeagueCache.active_season()
            if not active_season:
                await interaction.followup.send("❌ No active season.", ephemeral=True)
                return
            draft_divisions = LeagueCache.picks_divisions()
            if not draft_divisions:
                await interaction.followup.send(
                    "❌ No division runs a draft. Enable picks in Dashboard → Divisions → Rules.", ephemeral=True,
                )
                return

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
                title="🎯 Draft Picks",
                description=f"Status: {status_text}",
                color=status_color,
            )
            embed.add_field(name="Season", value=active_season['name'], inline=True)
            embed.add_field(name="State", value="Paused" if new_state else "Active", inline=True)
            embed.add_field(
                name="Divisions",
                value=", ".join(d.get('code') or d.get('name') for d in draft_divisions),
                inline=False,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"{'🔴 Picks paused' if new_state else '🟢 Picks resumed'} by {interaction.user.id} for {active_season['name']}")

        except Exception as e:
            logger.error(f"Error toggling picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to toggle picks.", ephemeral=True)


class PicksControllerCog(commands.Cog):
    """Admin cog for controlling draft picks."""

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
