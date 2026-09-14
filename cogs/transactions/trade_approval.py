"""
Trade execution and approval flow.

Accept/Decline buttons are DynamicItems keyed by proposal id, so they keep working after a
restart: every click reloads the proposal from the database, and execution happens atomically
inside the execute_trade_proposal database function.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import discord
from discord import ui

from cogs.score.roster_cache import RosterCache
from db import get_supabase
from db.transactions_repository import TradeExecutionError, TransactionsRepository
from utils.team_player_cache import TeamPlayerCache
from utils.embed_assets import (
    apply_league_badge,
    apply_team_badge,
    target_thumbnail,
)
from utils.embed_builder import EmbedBuilder

logger = logging.getLogger(__name__)

# Refusals after which the proposal can never succeed as written, so it gets cancelled.
UNRECOVERABLE_TRADE_ERRORS = frozenset({
    'TRADE_EXPIRED',
    'TRADE_PLAYER_UNAVAILABLE',
    'TRADE_PICK_UNAVAILABLE',
    'TRADE_HAS_NO_ASSETS',
    'TRADE_INVALID_ASSET',
})


@dataclass
class TradeContext:
    """A trade proposal with everything needed to render and resolve it."""

    proposal: Dict[str, Any]
    initiating_team: Dict[str, Any]
    counterparty_team: Dict[str, Any]
    trade_assets: List[Dict[str, Any]]
    league_code: Optional[str] = None

    @property
    def proposal_id(self) -> str:
        return self.proposal['id']

    def is_counterparty_owner(self, user_id: int) -> bool:
        owner_id = self.counterparty_team.get('owner_discord_id')
        return owner_id is not None and int(owner_id) == user_id


async def load_trade_context(supabase, proposal_id: str) -> Optional[TradeContext]:
    """Load a proposal, its assets, both teams, and the league code from the database."""
    proposal = TransactionsRepository(supabase).get_trade_with_assets(proposal_id)
    if not proposal:
        return None

    initiating_id = proposal['initiating_team_id']
    counterparty_id = proposal['counterparty_team_id']
    try:
        teams_response = (
            supabase
            .table('teams')
            .select('*')
            .in_('id', [initiating_id, counterparty_id])
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to load teams for trade %s: %s", proposal_id, exc)
        return None

    teams = {team['id']: team for team in teams_response.data or []}
    initiating_team = teams.get(initiating_id)
    counterparty_team = teams.get(counterparty_id)
    if not initiating_team or not counterparty_team:
        return None

    league_code = None
    division_id = initiating_team.get('division_id')
    if division_id:
        try:
            division_response = (
                supabase
                .table('divisions')
                .select('league_type')
                .eq('id', division_id)
                .limit(1)
                .execute()
            )
            if division_response.data:
                league_code = (division_response.data[0].get('league_type') or '').upper() or None
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("Failed to resolve league for trade %s: %s", proposal_id, exc)

    return TradeContext(
        proposal=proposal,
        initiating_team=initiating_team,
        counterparty_team=counterparty_team,
        trade_assets=proposal.get('trade_assets') or [],
        league_code=league_code,
    )


# ----------------------------------------------------------------------
# Embeds
# ----------------------------------------------------------------------
def _asset_lines(enriched_assets: List[Dict[str, Any]], direction: str) -> List[str]:
    lines = []
    for asset in enriched_assets:
        if asset['direction'] != direction:
            continue
        if asset['asset_type'] == 'player':
            lines.append(f"🧑 {asset.get('label', 'Unknown Player')}")
        elif asset['asset_type'] == 'pick':
            lines.append(f"📋 {asset.get('label', 'Unknown Pick')}")
    return lines


async def _executed_trade_builder(ctx: TradeContext, supabase, title: str, description: str) -> EmbedBuilder:
    from cogs.transactions.trade_utils import enrich_trade_assets
    enriched_assets = await enrich_trade_assets(ctx.trade_assets, supabase)

    builder = EmbedBuilder(
        title=title,
        description=description,
        force_color=0x57F287,  # Success green
        team_code=ctx.initiating_team.get('code'),
        division_code=ctx.league_code
    )

    outbound_lines = _asset_lines(enriched_assets, 'outbound')
    if outbound_lines:
        builder.add_field(f"From {ctx.initiating_team['name']}", "\n".join(outbound_lines), inline=False)

    inbound_lines = _asset_lines(enriched_assets, 'inbound')
    if inbound_lines:
        builder.add_field(f"From {ctx.counterparty_team['name']}", "\n".join(inbound_lines), inline=False)

    builder.add_timestamp()
    if ctx.initiating_team.get('code'):
        builder.add_team_image(ctx.initiating_team['code'], position="thumbnail")
    return builder


async def build_acceptance_embed(ctx: TradeContext, supabase) -> discord.Embed:
    builder = await _executed_trade_builder(
        ctx,
        supabase,
        title="Trade Executed",
        description=f"**{ctx.initiating_team['name']}** ↔ **{ctx.counterparty_team['name']}**",
    )
    embed, _ = builder.build_with_files()
    return embed


def build_decline_embed(ctx: TradeContext, actor: discord.abc.User) -> discord.Embed:
    builder = EmbedBuilder(
        title="Trade Declined",
        description=f"**{ctx.counterparty_team['name']}** declined the trade proposal.",
        force_color=0xED4245,  # Error red
        team_code=ctx.counterparty_team.get('code'),
        division_code=ctx.league_code
    )
    builder.add_field("Initiator", ctx.initiating_team['name'], inline=False)
    builder.add_field("Declined By", actor.mention, inline=False)
    builder.add_timestamp()
    if ctx.counterparty_team.get('code'):
        builder.add_team_image(ctx.counterparty_team['code'], position="thumbnail")
    embed, _ = builder.build_with_files()
    return embed


def build_cancelled_embed(ctx: TradeContext, reason: str) -> discord.Embed:
    builder = EmbedBuilder(
        title="Trade Cancelled",
        description=f"**{ctx.initiating_team['name']}** ↔ **{ctx.counterparty_team['name']}**",
        force_color=0xED4245,  # Error red
        team_code=ctx.initiating_team.get('code'),
        division_code=ctx.league_code
    )
    builder.add_field("Reason", reason, inline=False)
    builder.add_timestamp()
    embed, _ = builder.build_with_files()
    return embed


async def post_trade_executed_announcement(ctx: TradeContext, guild: Optional[discord.Guild], supabase) -> None:
    """Post a trade executed announcement to the trading channel and ping both team owners."""
    if guild is None:
        return
    try:
        from utils.config_manager import get_config_manager
        tx_config = await get_config_manager().get_trading_config(guild.id)
        channel_id = tx_config.get('channel_id')
        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        from utils.team_display import format_team_label_from_record
        initiator_label = format_team_label_from_record(ctx.initiating_team)
        counterparty_label = format_team_label_from_record(ctx.counterparty_team)

        builder = await _executed_trade_builder(
            ctx,
            supabase,
            title="🤝 Trade Executed",
            description=f"{initiator_label} ↔ {counterparty_label}",
        )
        embed, files = builder.build_with_files()

        mentions = []
        members_to_mention = []
        for owner_id in (ctx.initiating_team.get('owner_discord_id'), ctx.counterparty_team.get('owner_discord_id')):
            if not owner_id:
                continue
            mentions.append(f"<@{owner_id}>")
            member = guild.get_member(int(owner_id))
            if member:
                members_to_mention.append(member)

        await channel.send(
            content=" ".join(mentions) if mentions else None,
            embed=embed,
            files=files or None,
            allowed_mentions=discord.AllowedMentions(users=members_to_mention if members_to_mention else True),
        )
    except Exception as e:
        logger.warning(f"Failed to post trade executed announcement: {e}")


async def _edit_trade_message(interaction: discord.Interaction, ctx: TradeContext, embed: discord.Embed) -> None:
    """Replace the proposal message with its final state and remove the buttons."""
    message = interaction.message
    if message is None:
        return
    # Keep pointing at the logo already attached to the proposal message.
    if message.attachments:
        target_thumbnail(embed, message.attachments[0].filename)
    apply_team_badge(embed, ctx.initiating_team.get('code'))
    apply_league_badge(embed, ctx.league_code)
    try:
        await message.edit(embed=embed, view=None)
    except discord.HTTPException as exc:
        logger.warning("Failed to update trade message for %s: %s", ctx.proposal_id, exc)


async def _invalidate_trade_caches(ctx: TradeContext) -> None:
    RosterCache().invalidate()
    cache = TeamPlayerCache()
    await cache.invalidate_team(ctx.initiating_team['id'])
    await cache.invalidate_team(ctx.counterparty_team['id'])


async def _load_for_counterparty(interaction: discord.Interaction, proposal_id: str):
    """Load the trade and make sure the clicking user may respond. Returns (supabase, ctx) or None."""
    supabase = get_supabase()
    ctx = await load_trade_context(supabase, proposal_id)
    if ctx is None:
        await interaction.followup.send("❌ This trade proposal no longer exists.", ephemeral=True)
        return None

    if not ctx.is_counterparty_owner(interaction.user.id):
        await interaction.followup.send(
            "❌ Only the counterparty team owner can respond to this trade.",
            ephemeral=True,
        )
        return None
    return supabase, ctx


# ----------------------------------------------------------------------
# Persistent buttons
# ----------------------------------------------------------------------
class TradeAcceptButton(ui.DynamicItem[ui.Button], template=r'trade:accept:(?P<id>[0-9a-fA-F-]+)'):
    def __init__(self, proposal_id: str):
        super().__init__(
            ui.Button(
                label="Accept Trade",
                style=discord.ButtonStyle.success,
                custom_id=f"trade:accept:{proposal_id}",
            )
        )
        self.proposal_id = proposal_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match, /):
        return cls(match['id'])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loaded = await _load_for_counterparty(interaction, self.proposal_id)
        if loaded is None:
            return
        supabase, ctx = loaded

        logger.info(
            "🤝 Team %s approving trade proposal %s against %s",
            ctx.counterparty_team['code'],
            ctx.proposal_id,
            ctx.initiating_team['code']
        )

        repo = TransactionsRepository(supabase)
        try:
            repo.execute_trade_proposal(ctx.proposal_id, interaction.user.id)
        except TradeExecutionError as exc:
            if exc.code in UNRECOVERABLE_TRADE_ERRORS and repo.resolve_pending_trade(
                ctx.proposal_id, 'cancelled', reviewed_by=interaction.user.id
            ):
                await _edit_trade_message(interaction, ctx, build_cancelled_embed(ctx, exc.user_message))
            await interaction.followup.send(f"❌ {exc.user_message}", ephemeral=True)
            return

        await _invalidate_trade_caches(ctx)
        await _edit_trade_message(interaction, ctx, await build_acceptance_embed(ctx, supabase))
        await post_trade_executed_announcement(ctx, interaction.guild, supabase)
        await interaction.followup.send("✅ Trade executed successfully!", ephemeral=True)

        logger.info(
            "✅ Trade executed: %s → %s (proposal=%s)",
            ctx.initiating_team['code'],
            ctx.counterparty_team['code'],
            ctx.proposal_id
        )


class TradeDeclineButton(ui.DynamicItem[ui.Button], template=r'trade:decline:(?P<id>[0-9a-fA-F-]+)'):
    def __init__(self, proposal_id: str):
        super().__init__(
            ui.Button(
                label="Decline Trade",
                style=discord.ButtonStyle.danger,
                custom_id=f"trade:decline:{proposal_id}",
            )
        )
        self.proposal_id = proposal_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match, /):
        return cls(match['id'])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loaded = await _load_for_counterparty(interaction, self.proposal_id)
        if loaded is None:
            return
        supabase, ctx = loaded

        repo = TransactionsRepository(supabase)
        if not repo.resolve_pending_trade(ctx.proposal_id, 'declined', reviewed_by=interaction.user.id):
            await interaction.followup.send("⚠️ This trade proposal has already been resolved.", ephemeral=True)
            return

        logger.info(
            "❌ Team %s declined trade proposal %s from %s",
            ctx.counterparty_team['code'],
            ctx.proposal_id,
            ctx.initiating_team['code']
        )

        await _edit_trade_message(interaction, ctx, build_decline_embed(ctx, interaction.user))
        await interaction.followup.send("Trade declined.", ephemeral=True)


class TradeApprovalView(ui.View):
    """Accept/Decline buttons for the counterparty; handled by the registered DynamicItems."""

    def __init__(self, proposal_id: str):
        super().__init__(timeout=None)
        self.add_item(TradeAcceptButton(proposal_id))
        self.add_item(TradeDeclineButton(proposal_id))
