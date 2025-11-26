"""
Config Manager - Centralized configuration management for bot settings per guild
Handles bot_config table in Supabase with nested JSON for extensibility
"""

import discord
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class GuildConfig:
    """Singleton-like wrapper for guild config with caching"""

    def __init__(self, guild_id: int, supabase):
        self.guild_id = guild_id
        self.supabase = supabase
        self._cache: Dict[str, Any] = {}
        self._loaded = False

    async def load(self):
        """Load config from database"""
        try:
            result = self.supabase.table('bot_config').select('*').eq('guild_id', self.guild_id).execute()
            if result.data:
                self._cache = result.data[0].get('config', {})
            else:
                # Create empty config
                self.supabase.table('bot_config').insert({
                    'guild_id': self.guild_id,
                    'config': {}
                }).execute()
                self._cache = {}
            self._loaded = True
            logger.debug(f"✅ Loaded config for guild {self.guild_id}")
        except Exception as e:
            logger.error(f"❌ Failed to load config for guild {self.guild_id}: {e}")
            self._cache = {}
            self._loaded = True

    async def save(self):
        """Save config to database"""
        try:
            self.supabase.table('bot_config').update({
                'config': self._cache
            }).eq('guild_id', self.guild_id).execute()
            logger.debug(f"✅ Saved config for guild {self.guild_id}")
        except Exception as e:
            logger.error(f"❌ Failed to save config for guild {self.guild_id}: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value"""
        value = self._cache.get(key, default)
        # Debug logging for transaction_rules
        if key == "transaction_rules":
            logger.debug(f"🔍 GuildConfig.get('transaction_rules'): returning {value}")
        return value

    def set(self, key: str, value: Any):
        """Set config value (call save() to persist)"""
        self._cache[key] = value

    def get_nested(self, path: str, default: Any = None) -> Any:
        """Get nested config value using dot notation (e.g., 'timer.duration')"""
        keys = path.split('.')
        value = self._cache
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value

    def set_nested(self, path: str, value: Any):
        """Set nested config value using dot notation"""
        keys = path.split('.')
        current = self._cache

        # Navigate/create the path
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        """Get entire config"""
        return self._cache.copy()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for display"""
        return self._cache.copy()


class ConfigManager:
    """Manages guild configs with caching"""

    def __init__(self, supabase):
        self.supabase = supabase
        self._configs: Dict[int, GuildConfig] = {}

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        """Get or create config for guild"""
        if guild_id not in self._configs:
            config = GuildConfig(guild_id, self.supabase)
            await config.load()
            self._configs[guild_id] = config
        return self._configs[guild_id]

    async def get_config(self, guild_id: int) -> Dict[str, Any]:
        """Return the raw config dictionary for a guild."""
        config = await self.get_guild_config(guild_id)
        return config.get_all()

    async def set_config(self, guild_id: int, key: str, value: Any) -> None:
        """Set a top-level config key and persist it."""
        config = await self.get_guild_config(guild_id)
        config.set(key, value)
        await config.save()

    async def set_channel(
        self,
        guild_id: int,
        channel_type: str,
        channel: discord.TextChannel
    ):
        """Set a channel configuration (e.g., 'transactions', 'logs', 'timer')"""
        config = await self.get_guild_config(guild_id)
        
        if 'channels' not in config._cache:
            config.set('channels', {})

        channels = config.get('channels', {})
        channels[channel_type] = channel.id
        config.set('channels', channels)
        
        await config.save()
        logger.info(f"📌 Set {channel_type} channel to #{channel.name} in guild {guild_id}")

    async def get_channel_id(
        self,
        guild_id: int,
        channel_type: str
    ) -> Optional[int]:
        """Get a channel ID by type"""
        config = await self.get_guild_config(guild_id)
        channels = config.get('channels', {})
        return channels.get(channel_type)

    async def get_channel(
        self,
        guild: discord.Guild,
        channel_type: str
    ) -> Optional[discord.TextChannel]:
        """Get a channel object by type"""
        channel_id = await self.get_channel_id(guild.id, channel_type)
        if channel_id:
            return guild.get_channel(channel_id)
        return None

    async def set_timer_config(
        self,
        guild_id: int,
        duration_seconds: int = 600,
        auto_end: bool = True
    ):
        """Configure timer settings"""
        config = await self.get_guild_config(guild_id)
        
        if 'timer' not in config._cache:
            config.set('timer', {})

        timer_config = config.get('timer', {})
        timer_config['duration_seconds'] = duration_seconds
        timer_config['auto_end'] = auto_end
        config.set('timer', timer_config)
        
        await config.save()
        logger.info(f"⏱️  Set timer config for guild {guild_id}: {duration_seconds}s")

    async def get_timer_config(self, guild_id: int) -> Dict[str, Any]:
        """Get timer configuration"""
        config = await self.get_guild_config(guild_id)
        return config.get('timer', {
            'duration_seconds': 600,  # Default 10 minutes
            'auto_end': True
        })

    async def update_transactions_config(
        self,
        guild_id: int,
        **kwargs: Any
    ):
        """Update transactions configuration keys for a guild."""
        if not kwargs:
            return

        config = await self.get_guild_config(guild_id)
        tx_config = dict(config.get('transactions', {}) or {})

        for key, value in kwargs.items():
            if value is None:
                tx_config.pop(key, None)
            else:
                tx_config[key] = value

        config.set('transactions', tx_config)
        await config.save()
        logger.info(
            "💼 Updated transactions config for guild %s: %s",
            guild_id,
            ', '.join(f"{k}={v}" for k, v in kwargs.items())
        )

    async def get_transactions_config(self, guild_id: int) -> Dict[str, Any]:
        """Return transactions config with defaults applied."""
        defaults = {
            'channel_id': None,
            'default_expiry_minutes': 60,
            'require_league_approval': False
        }

        config = await self.get_guild_config(guild_id)
        stored = config.get('transactions', {}) or {}
        merged = defaults.copy()
        merged.update(stored)
        return merged

    async def get_trading_config(self, guild_id: int) -> Dict[str, Any]:
        """Return trading config with defaults applied."""
        defaults = {
            'channel_id': None,
        }

        config = await self.get_guild_config(guild_id)
        stored = config.get('trading', {}) or {}
        merged = defaults.copy()
        merged.update(stored)
        return merged

    async def set_role_requirement(
        self,
        guild_id: int,
        command: str,
        role_id: int
    ):
        """Set role requirement for a command"""
        config = await self.get_guild_config(guild_id)
        
        if 'role_requirements' not in config._cache:
            config.set('role_requirements', {})

        reqs = config.get('role_requirements', {})
        reqs[command] = role_id
        config.set('role_requirements', reqs)
        
        await config.save()
        logger.info(f"🔐 Set role requirement for {command} in guild {guild_id}")

    async def get_role_requirement(
        self,
        guild_id: int,
        command: str
    ) -> Optional[int]:
        """Get role requirement for a command"""
        config = await self.get_guild_config(guild_id)
        reqs = config.get('role_requirements', {})
        return reqs.get(command)

    def clear_cache(self, guild_id: int = None):
        """Clear cache for a guild or all guilds"""
        if guild_id:
            if guild_id in self._configs:
                del self._configs[guild_id]
            logger.debug(f"🗑️  Cleared config cache for guild {guild_id}")
        else:
            self._configs.clear()
            logger.debug("🗑️  Cleared all config cache")


# Global instance (initialized in bot.py)
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get global config manager instance"""
    if _config_manager is None:
        raise RuntimeError("ConfigManager not initialized. Call init_config_manager() first.")
    return _config_manager


def init_config_manager(supabase):
    """Initialize global config manager"""
    global _config_manager
    _config_manager = ConfigManager(supabase)
    logger.info("✅ ConfigManager initialized")
