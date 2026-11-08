"""How fast one user may run slash commands.

Every user gets a bucket of ``BURST`` command tokens that refills over ``PER_SECONDS``. Most commands cost one token;
commands that render images or run heavy queries declare a higher cost with ``extras=HEAVY``. Autocomplete and button
clicks are not commands and are never counted.
"""

from __future__ import annotations

import discord
from discord import app_commands

from utils.rate_limiter import RateLimiter

BURST = 8
PER_SECONDS = 30.0
HEAVY = {"cost": 3}  # for @app_commands.command(..., extras=HEAVY)


class LimitedCommandTree(app_commands.CommandTree):
    """Command tree that refuses a user's commands once they have spent their budget.

    The refusal is a ``CommandOnCooldown``, which the bot's global error handler already answers with the
    rate-limit message.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.limiter = RateLimiter(max_tokens=BURST, refill_rate=BURST / PER_SECONDS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.type is not discord.InteractionType.application_command:
            return True
        command = interaction.command
        cost = int(getattr(command, "extras", {}).get("cost", 1)) if command else 1
        key = str(interaction.user.id)
        if self.limiter.allow(key, cost):
            return True
        raise app_commands.CommandOnCooldown(
            app_commands.Cooldown(BURST, PER_SECONDS), self.limiter.get_retry_after(key, cost)
        )
