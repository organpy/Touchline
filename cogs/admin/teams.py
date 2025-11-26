"""Team Management UI for admin transactions dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord import app_commands

from db import queries
from db import get_supabase
from cogs.admin.common import LazyInitView
from cogs.admin.base import AdminBaseManager
from utils.team_player_cache import TeamPlayerCache

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
        """Build themed team management embed using unified builder."""
        from utils.embed_builder import EmbedBuilder
        builder = EmbedBuilder.generic(
            title="🏢 Team Management",
            description="Manage teams, owners, and team details.",
        )
        builder.add_field(
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
        # Augment footer with context
        footer_text = builder.embed.footer.text + " • Click a button below." if builder.embed.footer.text else "Click a button below."
        builder.set_footer_text(footer_text, icon_url=getattr(builder.embed.footer, 'icon_url', None))
        return builder.build()

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
        view = SetTeamOwnerView(self.supabase, self)
        await view.prepare()
        from utils.embed_builder import EmbedBuilder
        embed_builder = EmbedBuilder.generic(
            title="👑 Set Team Owner",
            description="Select a team, then select its new owner.",
        )
        await interaction.response.send_message(embed=embed_builder.build(), view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Edit Details", emoji="🏷️", style=discord.ButtonStyle.primary, row=0)
    async def edit_details(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = EditTeamDetailsView(self.supabase, self)
        await view.prepare()
        from utils.embed_builder import EmbedBuilder
        embed_builder = EmbedBuilder.generic(
            title="🏷️ Edit Team Details",
            description="Select a team to edit.",
        )
        await interaction.response.send_message(embed=embed_builder.build(), view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="Delete Team", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def delete_team(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = DeleteTeamView(self.supabase, self)
        await view.prepare()
        from utils.embed_builder import EmbedBuilder
        embed_builder = EmbedBuilder.error(
            title="🗑️ Delete Team",
            description="Select a team to delete (⚠️ **Cannot be undone**).",
        )
        await interaction.response.send_message(embed=embed_builder.build(), view=view, ephemeral=True)
        view.original_message = await interaction.original_response()

    @discord.ui.button(label="View Teams", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def view_teams(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            manager = AdminBaseManager()
            # ✅ FIXED: Now scoped to guild's divisions only
            teams = await manager.get_teams_for_guild(self.guild_id)
            if not teams:
                await interaction.followup.send("ℹ️ No teams found.", ephemeral=True)
                return

            from utils.embed_builder import EmbedBuilder
            embed_builder = EmbedBuilder.generic(
                title="📋 All Teams",
                description=f"Total: {len(teams)} team(s)",
            )

            for team in sorted(teams, key=lambda t: t.get('code', '')):
                owner_mention = f"<@{team['owner_discord_id']}>" if team.get('owner_discord_id') else "Unassigned"
                embed_builder.add_field(
                    name=f"{team.get('emoji', '🏀')} {team.get('name', 'Unknown')} (`{team.get('code', '?')}`)",
                    value=f"Owner: {owner_mention} | Division: {team.get('division_id', '?')}",
                    inline=False,
                )
            await interaction.followup.send(embed=embed_builder.build(), ephemeral=True)
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

        # Check if code already exists (globally unique)
        existing = await queries.get_team_by_code(self.supabase, team_code)
        if existing:
            await interaction.followup.send(f"❌ Team code `{team_code}` already exists.", ephemeral=True)
            return

        try:
            # ✅ FIXED: Get active season to extract division (this is a single-server bot)
            
            # Get active season which contains the division
            active_season = await queries.get_active_season(self.supabase)
            if not active_season:
                await interaction.followup.send(
                    "❌ No active season configured. Set up seasons first.",
                    ephemeral=True,
                )
                return
            
            division_id = active_season['division_id']
            
            # Insert team into correct division
            result = self.supabase.table('teams').insert({
                'name': team_name,
                'code': team_code,
                'division_id': division_id,  # ✅ Now scoped to division
                'owner_discord_id': None,  # Can be set later
            }).execute()

            if result.data:
                logger.info("✅ Team created: %s (%s) by %s in division %s", 
                           team_name, team_code, interaction.user.id, division_id)
                await TeamPlayerCache.refresh(self.supabase)
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


class SetTeamOwnerView(LazyInitView):
    """View for selecting team and owner."""

    def __init__(self, supabase: Any, parent_view: TeamActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self.selected_team: Optional[Dict[str, Any]] = None
        self.manager = AdminBaseManager()

    async def populate(self) -> None:
        """Initialize select dropdowns on first use."""
        try:
            # ✅ FIXED: Now scoped to guild's divisions only
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch teams for owner selection: %s", exc)
            return

        if not teams:
            team_select = discord.ui.Select(
                placeholder="No teams available",
                options=[discord.SelectOption(label="No teams found", value="none", description="Create a team first")],
                disabled=True,
            )
            self.add_item(team_select)
            return

        team_select = discord.ui.Select(
            placeholder="Select a team...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=t.get('name', 'Unknown')[:100],
                    value=t.get('code', '?'),
                    description=f"Owner: {t.get('owner_discord_id', 'Unassigned')}",
                )
                for t in sorted(teams, key=lambda x: x.get('code', ''))[:25]
            ],
            row=0,
        )
        team_select.callback = self.select_team_callback
        self.add_item(team_select)

    async def select_team_callback(self, interaction: discord.Interaction) -> None:
        """Handle team selection."""
        await interaction.response.defer(ephemeral=True)

        team_code = interaction.data['values'][0]
        try:
            # ✅ FIXED: Resolve team in guild context
            self.selected_team = await self.manager.resolve_team_in_guild(
                team_code, self.parent_view.guild_id
            )
            if not self.selected_team:
                await interaction.followup.send(
                    self.manager.error_team_not_in_guild(team_code),
                    ephemeral=True
                )
                return

            # Show owner selection
            owner_select = discord.ui.UserSelect(
                placeholder="Select new owner...",
                row=0,
            )

            async def owner_callback(owner_interaction: discord.Interaction) -> None:
                await owner_interaction.response.defer(ephemeral=True)
                new_owner_id = owner_interaction.data['values'][0]

                try:
                    update_result = self.supabase.table('teams').update({
                        'owner_discord_id': int(new_owner_id),
                    }).eq('id', self.selected_team['id']).execute()

                    if update_result.data:
                        logger.info(
                            "✅ Team owner updated: %s → %s by %s",
                            self.selected_team.get('code', '?'),
                            new_owner_id,
                            owner_interaction.user.id,
                        )
                        await TeamPlayerCache.refresh(self.supabase)
                        await owner_interaction.followup.send(
                            f"✅ Team **{self.selected_team.get('name')}** owner set to <@{new_owner_id}>",
                            ephemeral=True,
                        )
                        await self.parent_view.update_display()
                    else:
                        raise Exception("No data returned from update")
                except Exception as exc:  # pragma: no cover
                    logger.error("Failed to set team owner: %s", exc)
                    await owner_interaction.followup.send(
                        "❌ Failed to set team owner. Check server logs.",
                        ephemeral=True,
                    )

            owner_select.callback = owner_callback
            view = discord.ui.View()
            view.add_item(owner_select)

            await interaction.followup.send(
                f"Selected team: **{self.selected_team.get('name')}** (`{team_code}`)\n\nNow select the new owner:",
                view=view,
                ephemeral=True,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch team for owner change: %s", exc)
            await interaction.followup.send(
                "❌ Failed to fetch team. Check server logs.",
                ephemeral=True,
            )



class EditTeamDetailsView(LazyInitView):
    """View for editing team details."""

    def __init__(self, supabase: Any, parent_view: TeamActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self.manager = AdminBaseManager()

    async def populate(self) -> None:
        try:
            # ✅ FIXED: Now scoped to guild's divisions only
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch teams for editing: %s", exc)
            return

        if not teams:
            empty_select = discord.ui.Select(
                placeholder="No teams available",
                options=[discord.SelectOption(label="No teams found", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        team_select = discord.ui.Select(
            placeholder="Select a team to edit...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=t.get('name', 'Unknown')[:100],
                    value=t.get('code', '?'),
                )
                for t in sorted(teams, key=lambda x: x.get('code', ''))[:25]
            ],
            row=0,
        )
        team_select.callback = self.select_team_callback
        self.add_item(team_select)

    async def select_team_callback(self, interaction: discord.Interaction) -> None:
        """Handle team selection for editing."""
        team_code = interaction.data['values'][0]
        # ✅ FIXED: Resolve team in guild context
        team = await self.manager.resolve_team_in_guild(team_code, self.parent_view.guild_id)

        if not team:
            await interaction.response.send_message(
                self.manager.error_team_not_in_guild(team_code),
                ephemeral=True
            )
            return

        modal = EditTeamModal(self.supabase, self.parent_view, team)
        await interaction.response.send_modal(modal)



class EditTeamModal(discord.ui.Modal, title="Edit Team Details"):
    """Modal for editing team info."""

    team_name = discord.ui.TextInput(
        label="Team Name",
        max_length=100,
    )

    def __init__(self, supabase: Any, parent_view: TeamActionsView, team: Dict[str, Any]) -> None:
        super().__init__()
        self.supabase = supabase
        self.parent_view = parent_view
        self.team = team
        self.team_name.default = team.get('name', '')

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        new_name = self.team_name.value.strip()
        if not new_name:
            await interaction.followup.send("❌ Team name cannot be empty.", ephemeral=True)
            return

        try:
            result = self.supabase.table('teams').update({
                'name': new_name,
            }).eq('id', self.team['id']).execute()

            if result.data:
                logger.info(
                    "✅ Team name updated: %s → %s by %s",
                    self.team.get('code', '?'),
                    new_name,
                    interaction.user.id,
                )
                await TeamPlayerCache.refresh(self.supabase)
                await interaction.followup.send(
                    f"✅ Team name updated to **{new_name}**",
                    ephemeral=True,
                )
                await self.parent_view.update_display()
            else:
                raise Exception("No data returned from update")
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to update team name: %s", exc)
            await interaction.followup.send(
                "❌ Failed to update team. Check server logs.",
                ephemeral=True,
            )


class DeleteTeamView(LazyInitView):
    """View for deleting teams with confirmation."""

    def __init__(self, supabase: Any, parent_view: TeamActionsView) -> None:
        super().__init__(timeout=300)
        self.supabase = supabase
        self.parent_view = parent_view
        self.original_message: Optional[discord.Message] = None
        self.manager = AdminBaseManager()

    async def populate(self) -> None:
        try:
            # ✅ FIXED: Now scoped to guild's divisions only
            teams = await self.manager.get_teams_for_guild(self.parent_view.guild_id)
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to fetch teams for deletion: %s", exc)
            return

        if not teams:
            empty_select = discord.ui.Select(
                placeholder="No teams available",
                options=[discord.SelectOption(label="No teams found", value="none")],
                disabled=True,
            )
            self.add_item(empty_select)
            return

        team_select = discord.ui.Select(
            placeholder="Select a team to DELETE...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=t.get('name', 'Unknown')[:100],
                    value=t.get('code', '?'),
                    description="⚠️ DELETION IS PERMANENT",
                )
                for t in sorted(teams, key=lambda x: x.get('code', ''))[:25]
            ],
            row=0,
        )
        team_select.callback = self.select_team_callback
        self.add_item(team_select)

    async def select_team_callback(self, interaction: discord.Interaction) -> None:
        """Handle team selection for deletion."""
        team_code = interaction.data['values'][0]
        # ✅ FIXED: Resolve team in guild context
        team = await self.manager.resolve_team_in_guild(team_code, self.parent_view.guild_id)

        if not team:
            await interaction.response.send_message(
                self.manager.error_team_not_in_guild(team_code),
                ephemeral=True
            )
            return

        # Confirmation view
        class ConfirmDeleteView(discord.ui.View):
            def __init__(self, team_data: Dict[str, Any]) -> None:
                super().__init__(timeout=60)
                self.team_data = team_data

            @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
            async def confirm(self, confirm_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                await confirm_interaction.response.defer(ephemeral=True)
                try:
                    delete_result = self.supabase.table('teams').delete().eq('id', self.team_data['id']).execute()
                    logger.info(
                        "✅ Team deleted: %s (%s) by %s",
                        self.team_data.get('name', '?'),
                        self.team_data.get('code', '?'),
                        confirm_interaction.user.id,
                    )
                    await TeamPlayerCache.refresh(self.supabase)
                    await confirm_interaction.followup.send(
                        f"✅ Team **{self.team_data.get('name')}** deleted.",
                        ephemeral=True,
                    )
                    await self.parent_view.update_display()
                except Exception as exc:  # pragma: no cover
                    logger.error("Failed to delete team: %s", exc)
                    await confirm_interaction.followup.send(
                        "❌ Failed to delete team. Check server logs.",
                        ephemeral=True,
                    )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, cancel_interaction: discord.Interaction, _: discord.ui.Button) -> None:
                await cancel_interaction.response.defer(ephemeral=True)
                await cancel_interaction.followup.send("❌ Deletion cancelled.", ephemeral=True)

        confirm_view = ConfirmDeleteView(team)
        confirm_view.supabase = self.supabase
        confirm_view.parent_view = self.parent_view

        embed = discord.Embed(
            title="⚠️ Confirm Team Deletion",
            description=f"Are you sure you want to delete **{team.get('name')}** (`{team_code}`)?\n\n**This cannot be undone.**",
            color=discord.Color.red(),
        )

        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)

