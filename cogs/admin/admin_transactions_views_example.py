"""Team Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord import app_commands

from db import queries
from utils.team_player_cache import TeamPlayerCache
from db.db_utils import execute_async

if TYPE_CHECKING:  # pragma: no cover
    from .admin_transactions import AdminTransactionsCog

logger = logging.getLogger(__name__)


class TeamActionsView(discord.ui.View):
    """Buttons for team management actions."""

    def __init__(self, bot: Any, guild_id: int, supabase: Any) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.supabase = supabase
        self.original_message: Optional[discord.Message] = None

    def create_embed(self) -> discord.Embed:
        """Build team management embed."""
        embed = discord.Embed(
            title="🏢 Team Management",
            description="Manage teams, owners, and team details.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Available Actions",
            value=(
                "• ➕ **Add Team** - Create a new team\n"
                "• 👑 **Set Owner** - Change team owner\n"
                "• 🏷️ **Edit Details** - Update team info\n"
                "• 🗑️ **Delete Team** - Remove team\n"
                "• 📋 **View Teams** - List all teams"
            ),
            inline=False,
        )
        embed.set_footer(text="Click a button below.")
        return embed

    async def update_display(self) -> None:
        """Refresh the embed message."""
        if not self.original_message:
            return
        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update team management embed: %s", exc)

    @discord.ui.button(label="Add Team", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        modal = AddTeamModal(self.supabase, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Owner", emoji="👑", style=discord.ButtonStyle.primary, row=0)
    async def set_owner(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        modal = SetTeamOwnerModal(self.supabase, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Details", emoji="🏷️", style=discord.ButtonStyle.primary, row=0)
    async def edit_details(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "🏷️ Edit Team Details - Coming soon!",
            ephemeral=True,
        )

    @discord.ui.button(label="Delete Team", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def delete_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "🗑️ Delete Team - Coming soon!",
            ephemeral=True,
        )

    @discord.ui.button(label="View Teams", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def view_teams(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            teams = await queries.get_all_teams(self.supabase)
            if not teams:
                await interaction.followup.send("ℹ️ No teams found.", ephemeral=True)
                return

            embed = discord.Embed(
                title="📋 All Teams",
                description=f"Total: {len(teams)} team(s)",
                color=discord.Color.blue(),
            )

            for team in sorted(teams, key=lambda t: t.get('code', '')):
                owner_mention = f"<@{team['owner_discord_id']}>" if team.get('owner_discord_id') else "Unassigned"
                embed.add_field(
                    name=f"{team.get('emoji', '🏀')} {team.get('name', 'Unknown')} (`{team.get('code', '?')}`)",
                    value=f"Owner: {owner_mention} | Division: {team.get('league_type') or 'Unassigned'}",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch teams: %s", exc)
            await interaction.followup.send(
                "❌ Failed to fetch teams. Check server logs.",
                ephemeral=True,
            )

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Return to main dashboard."""
        await interaction.response.defer()
        self.stop()


class AddTeamModal(discord.ui.Modal, title="Add New Team"):
    """Modal for creating a new team."""

    team_name = discord.ui.TextInput(
        label="Team Name",
        placeholder="e.g., Golden Pandas",
        max_length=100,
    )
    team_code = discord.ui.TextInput(
        label="Team Code",
        placeholder="e.g., PANDAS (max 10 chars, uppercase)",
        max_length=10,
    )

    def __init__(self, supabase: Any, view: TeamActionsView) -> None:
        super().__init__()
        self.supabase = supabase
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        team_name = self.team_name.value.strip()
        team_code = self.team_code.value.strip().upper()

        # Validation
        if not team_name or not team_code:
            await interaction.followup.send("❌ Team name and code are required.", ephemeral=True)
            return

        if len(team_code) < 2:
            await interaction.followup.send("❌ Team code must be at least 2 characters.", ephemeral=True)
            return

        # Check if code already exists
        existing = await queries.get_team_by_code(self.supabase, team_code)
        if existing:
            await interaction.followup.send(f"❌ Team code `{team_code}` already exists.", ephemeral=True)
            return

        try:
            # Insert team (minimal fields, division/owner can be set after)
            result = await execute_async(self.supabase.table('teams').insert({
                'name': team_name,
                'code': team_code,
                'owner_discord_id': None,  # Can be set later
            }))

            if result.data:
                logger.info("✅ Team created: %s (%s) by %s", team_name, team_code, interaction.user.id)
                await interaction.followup.send(
                    f"✅ Team **{team_name}** (`{team_code}`) created successfully!",
                    ephemeral=True,
                )
                await self.view.update_display()
            else:
                raise Exception("No data returned from insert")
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to create team: %s", exc)
            await interaction.followup.send(
                "❌ Failed to create team. Check server logs.",
                ephemeral=True,
            )


class SetTeamOwnerModal(discord.ui.Modal, title="Set Team Owner"):
    """Modal for setting a team's owner."""

    def __init__(self, supabase: Any, view: TeamActionsView) -> None:
        super().__init__()
        self.supabase = supabase
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # This would ideally use a UserSelect dropdown in a View instead of a modal
        # For now, using placeholder logic
        await interaction.response.send_message(
            "🚧 Set Team Owner - Requires UserSelect dropdown. Coming in next phase!",
            ephemeral=True,
        )


# Example usage in AdminTransactionsCog:
# @discord.ui.button(label="Team Management", emoji="🏢", ...)
# async def teams(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
#     view = TeamActionsView(self.bot, interaction.guild.id, self.supabase)
#     embed = view.create_embed()
#     await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
#     view.original_message = await interaction.original_response()
