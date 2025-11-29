"""Admin picks controller - pause/resume draft picks."""

import logging
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_supabase, queries
from utils.config_manager import get_config_manager
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)


class PicksControllerView(discord.ui.View):
    """View for controlling draft picks (pause/resume)."""

    def __init__(self, bot: Any, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.original_message: Optional[discord.Message] = None

    def create_embed(self) -> discord.Embed:
        """Build the picks controller embed."""
        embed = discord.Embed(
            title="🎯 Draft Picks Controller",
            description="Control the draft pick process across all divisions",
            color=0x2B2D31,
        )
        embed.add_field(
            name="Controls",
            value=(
                "> **Pause Picks** - Prevent teams from making new picks\n"
                "> **Resume Picks** - Allow teams to make picks\n"
                "> **Status** - View current pick status"
            ),
            inline=False,
        )
        embed.set_footer(text="⚡ Select an option below")
        return embed

    @discord.ui.button(label="Pause Picks", style=discord.ButtonStyle.danger, row=0)
    async def pause_picks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Pause all draft picks."""
        if not await self._safe_defer(interaction):
            return

        try:
            config_mgr = get_config_manager()
            await config_mgr.set_guild_config(self.guild_id, "picks_paused", True)

            embed = discord.Embed(
                title="⏸️ Picks Paused",
                description="Teams can no longer make draft picks.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Status", value="**PAUSED**", inline=False)
            embed.set_footer(text="Resume to re-enable picking")

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"🔴 Draft picks PAUSED by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error pausing picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to pause picks.", ephemeral=True)

    @discord.ui.button(label="Resume Picks", style=discord.ButtonStyle.success, row=0)
    async def resume_picks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Resume draft picks."""
        if not await self._safe_defer(interaction):
            return

        try:
            config_mgr = get_config_manager()
            await config_mgr.set_guild_config(self.guild_id, "picks_paused", False)

            embed = discord.Embed(
                title="▶️ Picks Resumed",
                description="Teams can now make draft picks.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Status", value="**ACTIVE**", inline=False)
            embed.set_footer(text="Pause to stop picking")

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"🟢 Draft picks RESUMED by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Error resuming picks: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to resume picks.", ephemeral=True)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, row=0)
    async def status_picks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Show current pick status."""
        if not await self._safe_defer(interaction):
            return

        try:
            config_mgr = get_config_manager()
            config = await config_mgr.get_guild_config(self.guild_id)
            picks_paused = config.get("picks_paused", False)

            status_text = "**PAUSED** ⏸️" if picks_paused else "**ACTIVE** ▶️"
            status_color = discord.Color.red() if picks_paused else discord.Color.green()

            embed = discord.Embed(
                title="📊 Picks Status",
                description=f"Current status: {status_text}",
                color=status_color,
            )

            supabase = get_supabase()
            if supabase:
                try:
                    # Get active seasons
                    seasons_response = supabase.table('seasons').select('*').eq('active', True).execute()
                    if seasons_response.data:
                        for season in seasons_response.data:
                            season_name = season.get('name', 'Unknown')
                            embed.add_field(name=f"Season {season_name}", value="✓ Active", inline=True)
                except Exception as e:
                    logger.warning(f"Failed to fetch active seasons: {e}")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error getting pick status: {e}", exc_info=True)
            await interaction.followup.send("❌ Failed to get status.", ephemeral=True)

    async def _safe_defer(self, interaction: discord.Interaction) -> bool:
        """Attempt to defer the interaction; return False if it's expired."""
        try:
            await interaction.response.defer(ephemeral=True)
            return True
        except discord.NotFound:
            logger.warning(
                "Expired interaction token for picks controller (user=%s)",
                getattr(interaction.user, "id", "unknown"),
            )
            return False


class PicksControllerCog(commands.Cog):
    """Admin cog for controlling draft picks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="picks-controller",
        description="[ADMIN] Control draft picks (pause/resume)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def picks_controller(self, interaction: discord.Interaction) -> None:
        """Open the draft picks controller interface."""
        await interaction.response.defer(ephemeral=True)

        view = PicksControllerView(self.bot, interaction.guild_id)
        embed = view.create_embed()
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.original_message = message


async def setup(bot: commands.Bot) -> None:
    """Setup function for Discord.py cog loading."""
    await bot.add_cog(PicksControllerCog(bot))
    logger.info("✅ PicksControllerCog loaded")
