import os
import sys
import discord
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
            application_id=int(os.getenv("APPLICATION_ID", "0")) if os.getenv("APPLICATION_ID") else None
        )

        if BOT_OWNER_ID:
            self.owner_id = int(BOT_OWNER_ID)

    def discover_cogs(self, cogs_directory: str = "cogs") -> list:
        """
        Automatically discover all cogs in the cogs directory and subdirectories.
        Returns list of module paths ready for load_extension().
        """
        cog_extensions = []
        excluded_files = {'__init__.py', 'base.py', 'utils.py', 'models.py', 'views.py', 'roster_cache.py'}

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

        # Sync app commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} app commands")
        except Exception as e:
            logger.error(f"❌ Failed to sync app commands: {e}", exc_info=True)

    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f"✅ Bot ready! Logged in as {self.user}")
        logger.info(f"📡 Latency: {self.latency * 1000:.2f}ms")

    async def on_error(self, event, *args, **kwargs):
        """Global error handler"""
        logger.error(f"Error in {event}:", exc_info=True)

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
