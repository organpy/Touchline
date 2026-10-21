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


def test_commands_are_grouped_by_tag_and_name():
    from cogs.help.help import CommandCategory, category_for, strip_tag

    assert category_for("transfer-ownership", "[OWNER] Transfer team ownership") is CommandCategory.TEAM_OWNER
    assert category_for("report-score", "Report a game") is CommandCategory.SCORE
    assert category_for("dashboard", "[ADMIN] Run the league") is CommandCategory.ADMIN
    assert category_for("something", "No tag") is CommandCategory.OTHER
    assert strip_tag("[STATS] Standings") == "Standings"


@pytest.mark.asyncio
async def test_help_view_pages_through_a_category():
    from unittest.mock import AsyncMock, Mock

    from cogs.help.help import CommandCategory, HelpView

    categories = {cat: [] for cat in CommandCategory}
    categories[CommandCategory.STATS] = [(f"cmd{i}", "desc") for i in range(10)]
    view = HelpView(Mock(), categories)
    interaction = Mock()
    interaction.response.edit_message = AsyncMock()
    interaction.data = {"values": ["STATS"]}

    await view._choose(interaction)
    assert len(view.current_embed().fields) == 8
    await view._next(interaction)
    assert len(view.current_embed().fields) == 2
    await view._overview(interaction)
    assert view.current_category is None
