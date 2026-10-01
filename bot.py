import os
import sys
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from dotenv import load_dotenv
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize Supabase client for config manager
from db import get_supabase
from utils.config_manager import init_config_manager
from utils.team_player_cache import TeamPlayerCache
from utils.season_cache import SeasonCache
from cogs.score.roster_cache import RosterCache
from utils.health_check import HealthCheckServer
from utils.error_embeds import error_embed, permission_error_embed, rate_limit_embed, send_error

# ============================================================================
# CONFIG
# ============================================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID")

if not DISCORD_TOKEN:
    logger.error("❌ DISCORD_TOKEN not found in .env")
    sys.exit(1)

# ============================================================================
# BOT CLASS
# ============================================================================

class NBLBot(commands.Bot):
    """Main NBL Bot class with auto-cog discovery"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            # League commands only make sense inside a server; hide them from DMs and group DMs.
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
            application_id=int(os.getenv("APPLICATION_ID", "0")) if os.getenv("APPLICATION_ID") else None
        )

        if BOT_OWNER_ID:
            self.owner_id = int(BOT_OWNER_ID)

        try:
            self.supabase = get_supabase()
            logger.info("✅ Supabase client initialized")
        except Exception as exc:
            logger.error("❌ Failed to initialize Supabase client", exc_info=True)
            raise exc
        
        # Initialize health check server
        health_port = int(os.getenv("HEALTH_CHECK_PORT", "8080"))
        health_host = os.getenv("HEALTH_CHECK_HOST", "127.0.0.1")
        self.health_server = HealthCheckServer(self, port=health_port, host=health_host)

        self.tree.error(self.on_app_command_error)

    def discover_cogs(self, cogs_directory: str = "cogs") -> list:
        """
        Automatically discover all cogs in the cogs directory and subdirectories.
        Returns list of module paths ready for load_extension().
        """
        cog_extensions = []
        excluded_files = {
            '__init__.py', 'utils.py', 'models.py', 'roster_cache.py', 'access.py',
            # Shared base classes (not standalone cogs)
            'base.py',
            # Autocomplete modules (not standalone cogs)
            'autocomplete.py',
            # Transaction helper modules (not standalone cogs)
            'history.py', 'history_view.py', 'trade_approval.py', 'trade_views.py',
            'trade_utils.py', 'views.py', 'pick_trading.py', 'configuration.py', 'picks_view.py',
            'roster_transactions.py', 'trading.py', 'persistent_views.py',
            # Admin panel helper modules (deprecated/excluded)
            'admin_transactions_views_example.py', 'teams.py', 'rosters.py', 'picks.py', "common.py",
            # Stats helper modules (deprecated)
            'stats_commands.py',
        }

        for root, dirs, files in os.walk(cogs_directory):
            # Skip __pycache__ directories
            dirs[:] = [d for d in dirs if d != '__pycache__']

            for file in files:
                if file.endswith('.py') and file not in excluded_files:
                    file_path = os.path.join(root, file)
                    # Convert file path to module path
                    module_path = file_path.replace(os.sep, '.').replace('.py', '')
                    cog_extensions.append(module_path)

        return cog_extensions

    async def setup_hook(self):
        """Setup hook: discover and load all cogs"""
        logger.info("🔄 Initializing ConfigManager...")
        try:
            init_config_manager(self.supabase)
            logger.info("✅ ConfigManager initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize ConfigManager: {e}", exc_info=True)
        
        logger.info("🔄 Initializing TeamPlayerCache...")
        try:
            TeamPlayerCache.set_supabase(self.supabase)
            await TeamPlayerCache.refresh()
            logger.info("✅ TeamPlayerCache initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize TeamPlayerCache: {e}", exc_info=True)

        logger.info("🔄 Initializing SeasonCache...")
        try:
            SeasonCache.set_supabase(self.supabase)
            await SeasonCache.refresh()
            logger.info("✅ SeasonCache initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize SeasonCache: {e}", exc_info=True)
        
        logger.info("🔄 Initializing RosterCache...")
        try:
            roster_cache = RosterCache()
            await roster_cache.load_cache(self.supabase)
            logger.info("✅ RosterCache initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize RosterCache: {e}", exc_info=True)
        
        logger.info("🔄 Discovering cogs...")
        extensions = self.discover_cogs()

        if not extensions:
            logger.warning("⚠️  No cogs discovered")
        else:
            logger.info(f"📦 Found {len(extensions)} cogs: {', '.join(extensions)}")

        for extension in extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"✅ Loaded: {extension}")
            except Exception as e:
                logger.error(f"❌ Failed to load {extension}: {e}", exc_info=True)

        # Register persistent views
        try:
            from cogs.transactions.persistent_views import SigningAcceptButton, SigningDeclineButton
            from cogs.transactions.trade_approval import TradeAcceptButton, TradeDeclineButton
            self.add_dynamic_items(SigningAcceptButton, SigningDeclineButton, TradeAcceptButton, TradeDeclineButton)
            logger.info("✅ Persistent views registered")
        except Exception as e:
            logger.error(f"❌ Failed to register persistent views: {e}", exc_info=True)

        # Sync app commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} app commands")
        except Exception as e:
            logger.error(f"❌ Failed to sync app commands: {e}", exc_info=True)
        
        # Start health check server
        await self.health_server.start()

    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f"✅ Bot ready! Logged in as {self.user}")
        logger.info(f"📡 Latency: {self.latency * 1000:.2f}ms")

    
    async def close(self):
        """Cleanup on shutdown"""
        logger.info("🔄 Shutting down bot...")
        await self.health_server.stop()
        await super().close()
    async def on_error(self, event, *args, **kwargs):
        """Global error handler"""
        logger.error(f"Error in {event}:", exc_info=True)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Answer every failed slash command instead of leaving "The application did not respond"."""
        if isinstance(error, app_commands.CommandOnCooldown):
            embed = rate_limit_embed(int(error.retry_after) + 1)
        elif isinstance(error, app_commands.CheckFailure):
            embed = permission_error_embed()
        else:
            command_name = interaction.command.qualified_name if interaction.command else "unknown"
            original = getattr(error, "original", error)
            logger.error(
                "Unhandled error in /%s (user=%s guild=%s)",
                command_name,
                interaction.user.id,
                interaction.guild_id,
                exc_info=(type(original), original, original.__traceback__),
            )
            embed = error_embed(
                title="❌ Something went wrong",
                description="An unexpected error occurred while running this command.",
                hint="The error has been logged. Please try again or contact an admin.",
            )
        await send_error(interaction, embed, log=False)

# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Main entry point"""
    bot = NBLBot()

    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
