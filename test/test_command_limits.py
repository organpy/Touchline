"""Tests for the per-user slash command rate limit."""

from unittest.mock import Mock, patch

import discord
import pytest
from discord import app_commands

from utils import command_limits
from utils.command_limits import BURST, HEAVY, LimitedCommandTree


@pytest.fixture
def tree():
    return LimitedCommandTree(discord.Client(intents=discord.Intents.none()))


def _interaction(user_id=1, *, cost=None, kind=discord.InteractionType.application_command):
    interaction = Mock()
    interaction.type = kind
    interaction.user = Mock(id=user_id)
    interaction.command = Mock(extras={"cost": cost} if cost else {})
    return interaction


@pytest.mark.asyncio
async def test_a_user_can_run_a_burst_of_commands(tree):
    for _ in range(BURST):
        assert await tree.interaction_check(_interaction()) is True


@pytest.mark.asyncio
async def test_the_next_command_is_refused_with_a_retry_time(tree):
    for _ in range(BURST):
        await tree.interaction_check(_interaction())

    with pytest.raises(app_commands.CommandOnCooldown) as exc_info:
        await tree.interaction_check(_interaction())

    assert 0 < exc_info.value.retry_after <= command_limits.PER_SECONDS / BURST + 0.1


@pytest.mark.asyncio
async def test_heavy_commands_cost_more(tree):
    heavy = HEAVY["cost"]
    runs = BURST // heavy
    for _ in range(runs):
        assert await tree.interaction_check(_interaction(cost=heavy)) is True

    with pytest.raises(app_commands.CommandOnCooldown) as exc_info:
        await tree.interaction_check(_interaction(cost=heavy))

    # The wait covers the missing tokens for a heavy command, not just one token.
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_users_have_separate_budgets(tree):
    for _ in range(BURST):
        await tree.interaction_check(_interaction(user_id=1))

    assert await tree.interaction_check(_interaction(user_id=2)) is True


@pytest.mark.asyncio
async def test_autocomplete_is_never_counted(tree):
    for _ in range(BURST * 5):
        assert await tree.interaction_check(_interaction(kind=discord.InteractionType.autocomplete)) is True

    assert await tree.interaction_check(_interaction()) is True  # the budget is untouched


@pytest.mark.asyncio
async def test_budget_comes_back_over_time(tree):
    with patch("utils.rate_limiter.time.time") as clock:
        clock.return_value = 1000.0
        for _ in range(BURST):
            await tree.interaction_check(_interaction())
        with pytest.raises(app_commands.CommandOnCooldown):
            await tree.interaction_check(_interaction())

        clock.return_value = 1000.0 + command_limits.PER_SECONDS
        assert await tree.interaction_check(_interaction()) is True


def test_the_bot_uses_the_limited_tree():
    import os
    os.environ.setdefault('DISCORD_TOKEN', 'test-token')
    import bot as bot_module

    with patch.object(bot_module, 'get_supabase', return_value=Mock()):
        assert isinstance(bot_module.TouchlineBot().tree, LimitedCommandTree)


@pytest.mark.asyncio
async def test_the_global_handler_answers_a_refused_command_with_the_rate_limit_message():
    import os
    os.environ.setdefault('DISCORD_TOKEN', 'test-token')
    import bot as bot_module
    from unittest.mock import AsyncMock

    with patch.object(bot_module, 'get_supabase', return_value=Mock()):
        bot = bot_module.TouchlineBot()
    interaction = Mock()
    interaction.command = Mock(qualified_name="standings")
    interaction.user = Mock(id=1)
    interaction.response.is_done = Mock(return_value=False)
    interaction.response.send_message = AsyncMock()
    error = app_commands.CommandOnCooldown(app_commands.Cooldown(BURST, command_limits.PER_SECONDS), 4.2)

    await bot.on_app_command_error(interaction, error)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "Rate Limited" in embed.title
    assert "5 seconds" in embed.description


HEAVY_COMMANDS = {
    "player-card", "standings", "match-table", "season-summary", "history",
    "report-score", "report-stats", "report-stats-bulk",
}


@pytest.mark.asyncio
async def test_commands_that_render_images_or_run_heavy_queries_cost_more(loaded_bot):
    costs = {command.qualified_name.split()[-1]: command.extras.get("cost", 1) for command in loaded_bot.tree.walk_commands()
             if isinstance(command, app_commands.Command)}

    assert {name for name in HEAVY_COMMANDS if costs.get(name) != HEAVY["cost"]} == set()
    assert costs.get("leaderboard", 1) == 1  # ordinary commands stay at one token
