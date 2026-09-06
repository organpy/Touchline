"""Unified configuration utility for admin-facing panels and helpers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from copy import deepcopy

import discord

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)


class ConfigUtility:
    """Helper utilities for guild configuration and admin UX flows."""

    # ------------------------------------------------------------------
    # Core load/save helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def get_guild_config(bot: Any, guild_id: int) -> Dict[str, Any]:
        """Load configuration for a guild from Supabase and Firestore (if available)."""
        guild_id_str = str(guild_id)
        manager = get_config_manager()
        guild_config = await manager.get_guild_config(guild_id)
        supabase_config = guild_config.get_all() or {}

        # Prepare defaults used by UI panels.
        defaults: Dict[str, Any] = {
            "server_configured": False,
            "guild_name": "",
            "transactionsChannelId": "",
            "admin_role_id": "",
            "adminCommandsChannelId": "",
            # Namespaced config groups (stats/transactions etc.)
            "stats": {},
            "transactions": {
                "default_expiry_minutes": 60,
                "require_league_approval": False,
                "channel_id": "",
            },
            "trading": {
                "channel_id": "",
            },
        }

        merged = deepcopy(defaults)
        merged.update(supabase_config)

        # Normalise channel ids to strings for embed display.
        for field in ("transactionsChannelId", "adminCommandsChannelId"):
            if merged.get(field):
                merged[field] = str(merged[field])

        # Cache in bot memory for quick lookup by other utilities.
        if not hasattr(bot, "config"):
            bot.config = {"guilds": {}}
        bot.config.setdefault("guilds", {})[guild_id_str] = deepcopy(merged)
        return merged

    @staticmethod
    async def load_all_configs(bot: Any) -> None:
        """Prime the in-memory cache with all guild configs."""
        manager = get_config_manager()
        configs: Dict[str, Dict[str, Any]] = {}

        # Load all guild rows from Supabase.
        try:
            supabase_client = manager.supabase
            rows = supabase_client.table("bot_config").select("guild_id, config").execute()
            for row in rows.data or []:
                guild_id = str(row.get("guild_id"))
                config = row.get("config") or {}
                configs[guild_id] = config
        except Exception as exc:  # pragma: no cover - network path
            logger.error("❌ Failed to load configs from Supabase: %s", exc)

        bot.config = {"guilds": configs}
        logger.info("✅ Loaded configuration for %d guild(s)", len(configs))

    # ------------------------------------------------------------------
    # Validation & Permission Helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def check_admin_permissions(interaction: discord.Interaction, guild_config: Dict[str, Any]) -> bool:
        """Return True when the invoking user has admin rights."""
        if interaction.user.guild_permissions.administrator:
            return True

        admin_role_id = guild_config.get("admin_role_id")
        if admin_role_id:
            try:
                admin_role_id_int = int(admin_role_id)
            except (TypeError, ValueError):
                admin_role_id_int = None
            if admin_role_id_int and any(role.id == admin_role_id_int for role in interaction.user.roles):
                return True
        return False

    @staticmethod
    async def check_admin_channel_restriction(
        interaction: discord.Interaction,
        guild_config: Dict[str, Any]
    ) -> Tuple[bool, Optional[discord.Embed]]:
        """Ensure command is executed in configured admin channel (if set)."""
        channel_id = guild_config.get("adminCommandsChannelId")
        if channel_id and str(interaction.channel_id) != str(channel_id):
            embed = discord.Embed(
                title="❌ Wrong Channel",
                description=f"This command can only be used in <#{channel_id}>.",
                color=discord.Color.red()
            )
            return False, embed
        return True, None

    @staticmethod
    async def check_transaction_channel_restriction(
        interaction: discord.Interaction,
        guild_config: Dict[str, Any]
    ) -> Tuple[bool, Optional[discord.Embed]]:
        """Ensure command runs within the configured transactions channel."""
        channel_id = guild_config.get("transactionsChannelId")
        if channel_id and str(interaction.channel_id) != str(channel_id):
            embed = discord.Embed(
                title="❌ Wrong Channel",
                description=f"This command can only be used in <#{channel_id}>.",
                color=discord.Color.red()
            )
            return False, embed
        return True, None

    @staticmethod
    async def send_permission_denied_message(
        interaction: discord.Interaction,
        followup: bool = False
    ) -> None:
        """Notify user they lack permissions."""
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red()
        )
        if followup:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def save_guild_config(bot: Any, guild_id: int, config_data: Dict[str, Any]) -> None:
        """Persist configuration to Supabase (and Firestore when available)."""
        guild_id_str = str(guild_id)
        manager = get_config_manager()
        guild_config = await manager.get_guild_config(guild_id)

        # Update Supabase record.
        guild_config._cache = deepcopy(config_data)
        await guild_config.save()

        # Refresh bot cache to reflect latest state.
        if not hasattr(bot, "config"):
            bot.config = {"guilds": {}}
        bot.config.setdefault("guilds", {})[guild_id_str] = deepcopy(config_data)
        logger.info("💾 Configuration saved for guild %s", guild_id)

    # ------------------------------------------------------------------
    # Convenience getters/setters used by panels
    # ------------------------------------------------------------------
    @staticmethod
    async def get_blacklist_role_id(bot: Any, guild_id: int) -> str:
        config = await ConfigUtility.get_guild_config(bot, guild_id)
        return str(config.get("blacklist_role_id", "") or "")

    @staticmethod
    async def set_blacklist_role_id(bot: Any, guild_id: int, role_id: int) -> None:
        config = await ConfigUtility.get_guild_config(bot, guild_id)
        config["blacklist_role_id"] = str(role_id)
        await ConfigUtility.save_guild_config(bot, guild_id, config)

    @staticmethod
    async def get_trading_config(bot: Any, guild_id: int) -> Dict[str, Any]:
        """Get trading-specific configuration."""
        config = await ConfigUtility.get_guild_config(bot, guild_id)
        return config.get("trading", {}) or {}

    @staticmethod
    async def set_trading_channel(bot: Any, guild_id: int, channel_id: int) -> None:
        """Set the trading channel ID."""
        config = await ConfigUtility.get_guild_config(bot, guild_id)
        if "trading" not in config:
            config["trading"] = {}
        config["trading"]["channel_id"] = str(channel_id)
        await ConfigUtility.save_guild_config(bot, guild_id, config)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    @staticmethod
    def create_config_display_embed(
        config_data: Dict[str, Any],
        guild_name: Optional[str] = None,
        title: Optional[str] = None
    ) -> discord.Embed:
        """Build a standard embed representing guild configuration."""
        embed_title = title or "🛠️ Server Configuration"
        embed = discord.Embed(title=embed_title, color=discord.Color.blue())

        status = config_data.get("server_configured", False)
        status_emoji = "✅" if status else "⚠️"
        status_text = "Configured" if status else "Not Configured"
        embed.add_field(name="📊 Server Status", value=f"{status_emoji} {status_text}", inline=True)

        final_guild_name = guild_name or config_data.get("guild_name") or "Not Set"
        embed.add_field(name="🏰 Guild Name", value=final_guild_name, inline=True)

        admin_role_id = config_data.get("admin_role_id")
        admin_role_text = f"<@&{admin_role_id}>" if admin_role_id else "Not Set"
        embed.add_field(name="👑 Admin Role", value=admin_role_text, inline=True)

        transactions_channel = config_data.get("transactionsChannelId")
        transactions_channel_text = f"<#{transactions_channel}>" if transactions_channel else "Not Set"
        embed.add_field(name="💼 Transactions Channel", value=transactions_channel_text, inline=True)

        admin_channel = config_data.get("adminCommandsChannelId")
        admin_channel_text = f"<#{admin_channel}>" if admin_channel else "Not Set"
        embed.add_field(name="⚡ Admin Commands Channel", value=admin_channel_text, inline=True)

        trading_config = config_data.get("trading", {}) or {}
        trade_channel_id = trading_config.get("channel_id")
        trade_channel_text = f"<#{trade_channel_id}>" if trade_channel_id else "Not Set"
        embed.add_field(name="🔄 Trading Channel", value=trade_channel_text, inline=True)

        blacklist_role_id = config_data.get("blacklist_role_id")
        blacklist_role_text = f"<@&{blacklist_role_id}>" if blacklist_role_id else "Not Set"
        embed.add_field(name="🚫 Blacklist Role", value=blacklist_role_text, inline=True)

        # Transaction settings summary
        tx_config = config_data.get("transactions", {}) or {}
        expiry_minutes = tx_config.get("default_expiry_minutes", 60)
        require_approval = tx_config.get("require_league_approval", False)
        approval_text = "✅ Yes" if require_approval else "❌ No"
        embed.add_field(
            name="💳 Transaction Settings",
            value=f"Expiry: {expiry_minutes}m | Approval: {approval_text}",
            inline=False,
        )

        return embed
