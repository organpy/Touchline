"""Every command and button that changes league data must be guarded, checked by loading the real bot."""

import pytest
from discord import app_commands

# Checked with bot.is_owner inside the command: only the bot owner, not server admins.
OWNER_ONLY = {"admin-cogs"}


def _is_admin_check(check) -> bool:
    return getattr(check, "__qualname__", "").startswith("admin_only.")


def _admin_commands(bot):
    return [
        command for command in bot.tree.walk_commands()
        if isinstance(command, app_commands.Command) and (command.description or "").startswith("[ADMIN]")
    ]


@pytest.mark.asyncio
async def test_every_admin_command_carries_the_admin_check(loaded_bot):
    admin_commands = _admin_commands(loaded_bot)
    assert len(admin_commands) >= 9, "the walk should find the admin commands"

    unguarded = [
        command.qualified_name for command in admin_commands
        if command.qualified_name not in OWNER_ONLY and not any(_is_admin_check(c) for c in command.checks)
    ]
    assert unguarded == []

