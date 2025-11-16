"""
Help command cog.
Provides an interactive `/help` command to list all commands and show details for a specific command.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


async def _help_command_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    # Provide autocomplete suggestions for command names from the bot's command tree
    bot = interaction.client
    current_lower = (current or "").lower()
    choices: List[app_commands.Choice[str]] = []
    try:
        for cmd in sorted(bot.tree.walk_commands(), key=lambda c: c.name):
            label = f"/{cmd.name}"
            if current_lower and current_lower not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=cmd.name))
            if len(choices) >= 25:
                break
    except Exception as e:
        logger.error("Autocomplete help list generation failed: %s", e)
    return choices


class HelpCog(commands.Cog):
    """Provides a `/help` command that lists and explains available app commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show help for commands and usage")
    @app_commands.autocomplete(command=_help_command_autocomplete)
    async def help(
        self,
        interaction: discord.Interaction,
        command: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        if command:
            # Show detailed help for a specific command
            cmd_name = str(command).strip()
            target = None
            for cmd in self.bot.tree.walk_commands():
                if cmd.name.lower() == cmd_name.lower():
                    target = cmd
                    break
            if not target:
                await interaction.followup.send(f"❌ Command `{cmd_name}` not found.")
                return

            # Build an embed with command details
            embed = discord.Embed(title=f"/ {target.name}", description=target.description, color=discord.Color.green())
            if target.options:
                opts = []
                for opt in target.options:
                    opt_name = opt.name
                    opt_type = getattr(opt, 'type', 'string')
                    opt_required = getattr(opt, 'required', False)
                    opts.append(f"**{opt_name}** ({opt_type.__name__ if hasattr(opt_type, '__name__') else str(opt_type)}) - {'required' if opt_required else 'optional'}")
                embed.add_field(name="Options", value="\n".join(opts), inline=False)
            await interaction.followup.send(embed=embed)
            return

        # Otherwise list commands grouped by module or tag
        groups = {}
        for cmd in self.bot.tree.walk_commands():
            # Try to use a leading bracket tag in the description (e.g., [STATS])
            tag = None
            desc = cmd.description or ''
            if desc.startswith('[') and ']' in desc:
                tag = desc[1:desc.index(']')]
            if not tag:
                tag = cmd.cog_name or 'Other'
            groups.setdefault(tag, []).append((cmd.name, desc))

        embed = discord.Embed(title="Bot Help", description="List of available commands. Use `/help <command>` for details.", color=discord.Color.blurple())
        for tag, items in sorted(groups.items()):
            lines = [f"`/{name}` — {desc}" for name, desc in sorted(items)]
            embed.add_field(name=f"{tag} ({len(items)})", value='\n'.join(lines[:10]) + ("\n..." if len(lines) > 10 else ''), inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
