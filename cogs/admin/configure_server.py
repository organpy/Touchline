import logging
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.config_utility import ConfigUtility

logger = logging.getLogger(__name__)


class ConfigurationView(discord.ui.View):
    """Interactive configuration panel for guild administrators."""

    def __init__(self, bot: Any, guild_id: int, config_data: Dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.config_data = config_data.copy()
        self.original_config = config_data.copy()
        self.changes_made = False
        self.original_message: Optional[discord.Message] = None
        self.is_busy = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure the interacting user still has admin rights."""
        has_permission = await ConfigUtility.check_admin_permissions(interaction, self.config_data)
        if not has_permission:
            await interaction.response.send_message(
                "❌ You don't have permission to configure this server.",
                ephemeral=True,
            )
            return False
        return True

    def set_buttons_state(self, disabled: bool) -> None:
        """Enable or disable all buttons in the view."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = disabled

    def create_embed(self) -> discord.Embed:
        """Build the configuration summary embed."""
        embed = ConfigUtility.create_config_display_embed(
            self.config_data,
            title="🛠️ Server Configuration Panel",
        )
        embed.description = "Configure your server settings using the buttons below."

        if self.changes_made:
            embed.add_field(
                name="📝 Status",
                value="⚠️ **Unsaved Changes**\nClick 'Save Configuration' to apply changes.",
                inline=False,
            )

        if self.is_busy:
            embed.add_field(
                name="⏳ Status",
                value="🔄 Configuration in progress... Please wait.",
                inline=False,
            )

        embed.set_footer(
            text="Use the buttons below to configure different aspects of your server."
        )
        return embed

    async def update_display(self) -> None:
        """Refresh the main embed message."""
        if not self.original_message:
            return

        try:
            await self.original_message.edit(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Failed to update configuration panel: %s", exc)

    def mark_changes(self) -> None:
        """Flag that unsaved changes exist."""
        self.changes_made = True
        logger.info("Changes marked for guild %s", self.guild_id)

    @discord.ui.button(label="Admin Role", emoji="👑", style=discord.ButtonStyle.primary, row=0)
    async def admin_role(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        self.is_busy = True
        self.set_buttons_state(True)
        await self.update_display()

        try:
            view = RoleSelectView()
            embed = discord.Embed(
                title="👑 Select Admin Role",
                description="Choose the role that should have admin permissions for this bot.",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if view.selected_role_id:
                self.config_data["admin_role_id"] = view.selected_role_id
                self.mark_changes()
        finally:
            self.is_busy = False
            self.set_buttons_state(False)
            await self.update_display()

    @discord.ui.button(label="Transactions Channel", emoji="💼", style=discord.ButtonStyle.primary, row=0)
    async def transactions_channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_channel_selection(
            interaction,
            "Transactions Channel",
            "transactionsChannelId",
            "💼 Select Transactions Channel",
            "Choose the channel where transaction notifications will be sent.",
        )

    @discord.ui.button(label="Admin Commands Channel", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def admin_commands_channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_channel_selection(
            interaction,
            "Admin Commands Channel",
            "adminCommandsChannelId",
            "⚡ Select Admin Commands Channel",
            "Choose the channel where admin commands can be used.",
        )

    @discord.ui.button(label="Trading Channel", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def trading_channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_trading_channel_selection(interaction)

    async def _handle_channel_selection(
        self,
        interaction: discord.Interaction,
        display_name: str,
        config_key: str,
        modal_title: str,
        modal_description: str,
    ) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        self.is_busy = True
        self.set_buttons_state(True)
        await self.update_display()

        try:
            view = ChannelSelectView(display_name)
            embed = discord.Embed(
                title=modal_title,
                description=modal_description,
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if view.selected_channel_id:
                self.config_data[config_key] = view.selected_channel_id
                self.mark_changes()
        finally:
            self.is_busy = False
            self.set_buttons_state(False)
            await self.update_display()

    async def _handle_trading_channel_selection(self, interaction: discord.Interaction) -> None:
        """Handle trading channel selection with nested config."""
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        self.is_busy = True
        self.set_buttons_state(True)
        await self.update_display()

        try:
            view = ChannelSelectView("Trading Channel")
            embed = discord.Embed(
                title="🔄 Select Trading Channel",
                description="Choose the channel where trade proposals will be posted.",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if view.selected_channel_id:
                if "trading" not in self.config_data:
                    self.config_data["trading"] = {}
                self.config_data["trading"]["channel_id"] = view.selected_channel_id
                self.mark_changes()
        finally:
            self.is_busy = False
            self.set_buttons_state(False)
            await self.update_display()

    @discord.ui.button(label="Transaction Settings", emoji="⚙️", style=discord.ButtonStyle.primary, row=1)
    async def transaction_settings(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        self.is_busy = True
        self.set_buttons_state(True)
        await self.update_display()

        try:
            tx_config = self.config_data.get("transactions", {}) or {}
            view = TransactionConfigView(tx_config)
            embed = discord.Embed(
                title="⚙️ Transaction Settings",
                description="Configure signing offer defaults and behavior.",
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if view.changes_made:
                self.config_data["transactions"] = view.tx_config
                self.mark_changes()
        finally:
            self.is_busy = False
            self.set_buttons_state(False)
            await self.update_display()

    @discord.ui.button(label="Games Limits", emoji="🎮", style=discord.ButtonStyle.primary, row=1)
    async def games_limits(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Configure games limit per division."""
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        self.is_busy = True
        self.set_buttons_state(True)
        await self.update_display()

        try:
            games_config = self.config_data.get("games_limits", {}) or {}
            view = GamesLimitConfigView(games_config)
            embed = view.create_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if view.changes_made:
                self.config_data["games_limits"] = view.games_config
                self.mark_changes()
        finally:
            self.is_busy = False
            self.set_buttons_state(False)
            await self.update_display()

    @discord.ui.button(label="Save Configuration", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save_config(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        if not self.changes_made:
            await interaction.response.send_message("ℹ️ No changes to save.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            required_fields = ["admin_role_id"]
            if all(self.config_data.get(field) for field in required_fields):
                self.config_data["server_configured"] = True

            await ConfigUtility.save_guild_config(self.bot, self.guild_id, self.config_data)
            self.changes_made = False

            success_embed = discord.Embed(
                title="✅ Configuration Saved",
                description="Your server configuration has been saved successfully!",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=success_embed, ephemeral=True)
            await self.update_display()
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Error saving configuration: %s", exc)
            failure_embed = discord.Embed(
                title="❌ Save Failed",
                description="An error occurred while saving the configuration. Please try again.",
                color=discord.Color.red(),
            )
            try:
                await interaction.followup.send(embed=failure_embed, ephemeral=True)
            except Exception:  # pragma: no cover - network path
                logger.exception("Failed to notify user about configuration save failure")

    @discord.ui.button(label="Reset Configuration", emoji="🔄", style=discord.ButtonStyle.danger, row=1)
    async def reset_config(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        view = ConfirmResetView()
        embed = discord.Embed(
            title="⚠️ Confirm Reset",
            description=(
                "Are you sure you want to reset all configuration to default values?\n\n"
                "**This action cannot be undone!**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await view.wait()

        if view.confirmed:
            self.config_data = {
                "server_configured": False,
                "guild_name": self.config_data.get("guild_name", ""),
                "transactionsChannelId": "",
                "admin_role_id": "",
                "adminCommandsChannelId": "",
                "trading": {
                    "channel_id": "",
                },
            }
            self.mark_changes()

            confirmation_embed = discord.Embed(
                title="🔄 Configuration Reset",
                description="Configuration has been reset to default values. Don't forget to save!",
                color=discord.Color.orange(),
            )
            await interaction.edit_original_response(embed=confirmation_embed, view=None)
            await self.update_display()

    @discord.ui.button(label="Close Panel", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
    async def close_panel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.is_busy:
            await interaction.response.send_message(
                "⏳ Please wait for the current operation to complete.",
                ephemeral=True,
            )
            return

        if self.changes_made:
            view = ConfirmCloseView()
            embed = discord.Embed(
                title="⚠️ Unsaved Changes",
                description="You have unsaved changes. Are you sure you want to close the configuration panel?",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if not view.confirmed:
                return

        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="✅ Configuration Panel Closed",
            description="The configuration panel has been closed.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class RoleSelectView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.selected_role_id: Optional[str] = None

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select an admin role...",
        min_values=1,
        max_values=1,
    )
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        self.selected_role_id = str(role.id)

        embed = discord.Embed(
            title="✅ Admin Role Selected",
            description=f"Selected admin role: {role.mention}",
            color=discord.Color.green(),
        )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class ChannelSelectView(discord.ui.View):
    def __init__(self, display_name: str) -> None:
        super().__init__(timeout=60)
        self.display_name = display_name
        self.selected_channel_id: Optional[str] = None

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select a channel...",
        channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        channel = select.values[0]
        self.selected_channel_id = str(channel.id)

        embed = discord.Embed(
            title=f"✅ {self.display_name} Selected",
            description=f"Selected channel: {channel.mention}",
            color=discord.Color.green(),
        )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class TransactionConfigView(discord.ui.View):
    """Configure transaction-specific settings (expiry, approvals, etc.)."""

    def __init__(self, tx_config: Dict[str, Any]) -> None:
        super().__init__(timeout=120)
        self.tx_config = tx_config.copy()
        self.changes_made = False

    def create_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚙️ Transaction Settings",
            description="Manage signing offer defaults and behavior.",
            color=discord.Color.gold(),
        )
        expiry_minutes = self.tx_config.get("default_expiry_minutes", 60)
        embed.add_field(
            name="⏱️ Default Offer Expiry",
            value=f"{expiry_minutes} minute(s)",
            inline=True,
        )
        require_approval = self.tx_config.get("require_league_approval", False)
        approval_text = "✅ Yes" if require_approval else "❌ No"
        embed.add_field(
            name="🔐 Require League Approval",
            value=approval_text,
            inline=True,
        )
        if self.changes_made:
            embed.add_field(name="📝 Status", value="⚠️ Changes pending", inline=False)
        return embed

    async def update_embed(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.edit_original_response(embed=self.create_embed(), view=self)
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Failed to refresh transaction settings embed: %s", exc)

    @discord.ui.button(label="Set Expiry (minutes)", emoji="⏱️", style=discord.ButtonStyle.primary)
    async def set_expiry(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ExpiryModal(self))

    @discord.ui.button(label="Toggle Approval Requirement", emoji="🔐", style=discord.ButtonStyle.primary)
    async def toggle_approval(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        current = self.tx_config.get("require_league_approval", False)
        self.tx_config["require_league_approval"] = not current
        self.changes_made = True
        new_value = "enabled" if not current else "disabled"
        await interaction.response.defer()
        await self.update_embed(interaction)

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="✅ Transaction Settings Updated",
            description="Changes will be applied when you save the main configuration.",
            color=discord.Color.green(),
        )
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class ExpiryModal(discord.ui.Modal, title="Set Default Offer Expiry"):
    """Modal for setting transaction offer expiry."""

    expiry_input = discord.ui.TextInput(
        label="Expiry (minutes)",
        placeholder="60",
        default="60",
        min_length=1,
        max_length=4,
    )

    def __init__(self, view: TransactionConfigView) -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            minutes = int(self.expiry_input.value)
            if minutes < 1 or minutes > 1440:
                await interaction.response.send_message(
                    "❌ Expiry must be between 1 and 1440 minutes.",
                    ephemeral=True,
                )
                return

            self.view.tx_config["default_expiry_minutes"] = minutes
            self.view.changes_made = True

            await interaction.response.defer()
            await self.view.update_embed(interaction)
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number.",
                ephemeral=True,
            )



class ConfirmResetView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=30)
        self.confirmed = False

    @discord.ui.button(label="Yes, Reset", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm_reset(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel_reset(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = False
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="❌ Reset Cancelled",
            description="Configuration reset has been cancelled.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class ConfirmCloseView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=30)
        self.confirmed = False

    @discord.ui.button(label="Yes, Close", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm_close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Keep Open", emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.confirmed = False
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="✅ Staying Open",
            description="Configuration panel will remain open.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class ServerConfigCog(commands.Cog):
    """Cog exposing the interactive server configuration panel."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="configure-server",
        description="[ADMIN] Configure server settings with an interactive panel",
    )
    async def configure_server(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used inside a Discord server.",
                ephemeral=True,
            )
            return

        guild_config = await ConfigUtility.get_guild_config(self.bot, interaction.guild.id)

        channel_allowed, channel_error_embed = await ConfigUtility.check_admin_channel_restriction(
            interaction, guild_config
        )
        if not channel_allowed:
            await interaction.response.send_message(embed=channel_error_embed, ephemeral=True)
            return

        has_permission = await ConfigUtility.check_admin_permissions(interaction, guild_config)
        if not has_permission:
            await ConfigUtility.send_permission_denied_message(interaction)
            return

        guild_config["guild_name"] = interaction.guild.name

        view = ConfigurationView(self.bot, interaction.guild.id, guild_config)
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            view.original_message = await interaction.original_response()
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Unable to fetch original response for configuration panel: %s", exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerConfigCog(bot))
    try:
        await ConfigUtility.load_all_configs(bot)
    except Exception as exc:  # pragma: no cover - network path
        logger.error("Failed to prime configuration cache: %s", exc)
