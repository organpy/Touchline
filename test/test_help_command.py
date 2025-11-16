import pytest
import discord
from discord.ext import commands
from cogs.help.help import HelpCog


@pytest.mark.asyncio
async def test_help_command_registers():
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    await bot.add_cog(HelpCog(bot))
    cmd_names = [cmd.name for cmd in bot.tree.walk_commands()]
    assert 'help' in cmd_names
