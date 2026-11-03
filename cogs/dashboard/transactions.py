"""Dashboard: transaction tools.

Each tool is an existing panel with its own multi-step flow, so it opens in a separate ephemeral
message and the dashboard stays where it is.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import discord

from utils.team_player_cache import TeamPlayerCache

from .core import EMBED_COLOR, DashboardPage

logger = logging.getLogger(__name__)


class TransactionsPage(DashboardPage):
    title = "Transactions"

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title="💼 Transactions", color=EMBED_COLOR)
        embed.description = "Each tool opens in its own message."
        embed.add_field(name="People", value="`Players` · `Rosters` · `Draft picks`", inline=False)
        embed.add_field(name="Moves", value="`Trades` · `Offers` · `Overrides`", inline=False)
        embed.add_field(name="Controls", value="`Rules` · `Blocker` · `Audit`", inline=False)
        return embed

    def build(self) -> None:
        self.button("Players", self._launch(self._players), emoji="👥", row=0)
        self.button("Rosters", self._launch(self._rosters), emoji="📋", row=0)
        self.button("Draft picks", self._launch(self._picks), emoji="🎟️", row=0)
        self.button("Trades", self._launch(self._trades), emoji="🔄", row=1)
        self.button("Offers", self._launch(self._offers), emoji="✉️", row=1)
        self.button("Overrides", self._launch(self._overrides), emoji="🛠️", row=1)
        self.button("Rules", self._launch(self._rules), emoji="⚙️", row=2)
        self.button("Blocker", self._launch(self._blocker), emoji="🔐", row=2)
        self.button("Audit", self._launch(self._audit), emoji="🧾", row=2)

    def _launch(self, factory: Callable[[discord.Interaction], Awaitable[Any]]):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=False)
            view, embed = await factory(interaction)
            message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
            for attribute in ("original_message", "message"):
                if hasattr(view, attribute):
                    setattr(view, attribute, message)
        return callback

    @property
    def _bot(self) -> Any:
        return self.session.bot

    async def _players(self, interaction: discord.Interaction):
        from cogs.admin.players import PlayerActionsView

        view = PlayerActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _rosters(self, interaction: discord.Interaction):
        from cogs.admin.rosters import RosterActionsView

        view = RosterActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _picks(self, interaction: discord.Interaction):
        from cogs.admin.picks import PicksActionsView

        view = PicksActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _trades(self, interaction: discord.Interaction):
        from cogs.admin.trades import TradeManagementView

        view = TradeManagementView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _offers(self, interaction: discord.Interaction):
        from cogs.admin.pending_offers import PendingOffersActionsView

        view = PendingOffersActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _overrides(self, interaction: discord.Interaction):
        from cogs.admin.overrides import OverridesActionsView

        view = OverridesActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _rules(self, interaction: discord.Interaction):
        from cogs.admin.transaction_settings import TransactionSettingsView

        view = TransactionSettingsView(self._bot, self.session.guild_id)
        await view.prepare()
        return view, view.create_embed()

    async def _audit(self, interaction: discord.Interaction):
        from cogs.admin.audit_reports import AuditReportsActionsView

        view = AuditReportsActionsView(self._bot, self.session.guild_id, self.session.supabase)
        return view, view.create_embed()

    async def _blocker(self, interaction: discord.Interaction):
        from cogs.admin.transaction_blocker import TransactionBlockerSummaryView
        from utils.config_manager import get_config_manager

        config = await get_config_manager().get_guild_config(self.session.guild_id)
        blocks = config.get("transaction_blocks", {})
        view = TransactionBlockerSummaryView(self.session.guild_id, self.session.supabase, blocks)
        teams = TeamPlayerCache.get_all_teams()
        embed = discord.Embed(
            title="🔐 Transaction Blocker",
            description="Block teams from signings, trades and releases mid-season.",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Current status",
            value=f"✅ Enabled: {len(teams) - len(blocks)} teams\n🚫 Blocked: {len(blocks)} teams",
            inline=False,
        )
        return view, embed


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
