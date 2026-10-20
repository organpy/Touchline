"""Dashboard: server settings (admin role, channels, league name, signing offers).

Every change is saved straight away.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import discord

from db.league_repository import LeagueRuleError
from utils import board_events, branding
from utils.config_utility import ConfigUtility

from .core import EMBED_COLOR, DashboardPage, FormModal, parse_int

# kind → (label, description, how it is stored)
CHANNELS: Dict[str, tuple] = {
    "transactions": ("Transactions", "Signings, releases and ownership changes are announced here", ("transactionsChannelId",)),
    "admin": ("Admin commands", "Admin commands only work here (leave unset for anywhere)", ("adminCommandsChannelId",)),
    "trading": ("Trading", "Trade proposals are posted here", ("trading", "channel_id")),
    "announcements": ("Announcements", "Playoff results, champions and promotions are announced here", ("announcements_channel_id",)),
    "board": ("League board", "Standings and brackets that update themselves after every result", ("board", "channel_id")),
}


def read_channel(config: Dict[str, Any], kind: str) -> Optional[str]:
    path = CHANNELS[kind][2]
    value: Any = config
    for key in path:
        value = (value or {}).get(key) if isinstance(value, dict) else None
    return str(value) if value else None


def write_channel(config: Dict[str, Any], kind: str, channel_id: Optional[str]) -> None:
    *parents, leaf = CHANNELS[kind][2]
    target = config
    for key in parents:
        if not isinstance(target.get(key), dict):
            target[key] = {}
        target = target[key]
    target[leaf] = channel_id or ""


class SettingsPage(DashboardPage):
    title = "Settings"

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.channel_kind = "transactions"

    async def load(self) -> None:
        self.config = await ConfigUtility.get_guild_config(self.session.bot, self.session.guild_id)

    def embed(self) -> discord.Embed:
        c = self.config
        embed = discord.Embed(title="⚙️ Settings", color=EMBED_COLOR)
        league = c.get(branding.LEAGUE_NAME_KEY) or f"{branding.DEFAULT_LEAGUE_NAME} (default)"
        role = f"<@&{c['admin_role_id']}>" if c.get("admin_role_id") else "Not set (server administrators only)"
        embed.add_field(name="League name", value=league, inline=True)
        embed.add_field(name="Admin role", value=role, inline=True)
        channels = []
        for kind, (label, _, _) in CHANNELS.items():
            channel_id = read_channel(c, kind)
            channels.append(f"**{label}:** {f'<#{channel_id}>' if channel_id else 'Not set'}")
        embed.add_field(name="Channels", value="\n".join(channels), inline=False)
        tx = c.get("transactions", {}) or {}
        approval = "required" if tx.get("require_league_approval") else "not required"
        embed.add_field(
            name="Signing offers",
            value=f"Expire after {tx.get('default_expiry_minutes', 60)} min · league approval {approval}",
            inline=False,
        )
        embed.set_footer(text="Pick a channel type, then the channel. Changes save immediately.")
        return embed

    def build(self) -> None:
        role_select = discord.ui.RoleSelect(placeholder="Admin role...", min_values=0, max_values=1, row=0)
        if self.config.get("admin_role_id"):
            role_select.default_values = [discord.Object(id=int(self.config["admin_role_id"]))]
        role_select.callback = lambda interaction: self._set_role(interaction, role_select)
        self.add_item(role_select)

        self.select(
            "Channel to set...",
            [
                discord.SelectOption(label=label, value=kind, description=description[:100], default=kind == self.channel_kind)
                for kind, (label, description, _) in CHANNELS.items()
            ],
            self._pick_kind,
            row=1,
        )
        label = CHANNELS[self.channel_kind][0]
        channel_select = discord.ui.ChannelSelect(
            placeholder=f"{label} channel...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=0,
            max_values=1,
            row=2,
        )
        current = read_channel(self.config, self.channel_kind)
        if current:
            channel_select.default_values = [discord.Object(id=int(current))]
        channel_select.callback = lambda interaction: self._set_channel(interaction, channel_select)
        self.add_item(channel_select)

        self.button("League name", self._league_name, emoji="🏷️", row=3)
        self.button("Signing offers", self._offers, emoji="✉️", row=3)
        self.button("Refresh board", self._refresh_board, emoji="🔄", row=3, disabled=not read_channel(self.config, "board"))

    async def _refresh_board(self, interaction: discord.Interaction) -> None:
        board_events.league_changed(delay=0)
        self.notice = "🔄 The league board is refreshing."
        await self.show(interaction)

    async def _save(self, interaction: discord.Interaction, success: str) -> None:
        config = self.config

        async def change() -> None:
            if config.get("admin_role_id"):
                config["server_configured"] = True
            await ConfigUtility.save_guild_config(self.session.bot, self.session.guild_id, config)

        await self.apply(interaction, change, success)

    async def _set_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0] if select.values else None
        self.config["admin_role_id"] = str(role.id) if role else ""
        await self._save(interaction, f"Admin role set to {role.mention}." if role else "Admin role cleared.")

    async def _pick_kind(self, interaction: discord.Interaction, kind: str) -> None:
        self.channel_kind = kind
        await self.show(interaction)

    async def _set_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        channel = select.values[0] if select.values else None
        write_channel(self.config, self.channel_kind, str(channel.id) if channel else None)
        label = CHANNELS[self.channel_kind][0]
        await self._save(interaction, f"{label} channel set to {channel.mention}." if channel else f"{label} channel cleared.")

    async def _league_name(self, interaction: discord.Interaction) -> None:
        modal = FormModal("League name", self._submit_league_name)
        modal.league = modal.text(
            "League name (blank = Touchline)",
            default=self.config.get(branding.LEAGUE_NAME_KEY),
            placeholder="e.g. National Bobble League",
            required=False,
            max_length=branding.LEAGUE_NAME_MAX_LENGTH,
        )
        await interaction.response.send_modal(modal)

    async def _submit_league_name(self, interaction: discord.Interaction, modal: FormModal) -> None:
        value = (modal.league.value or "").strip()
        self.config[branding.LEAGUE_NAME_KEY] = value
        await self._save(interaction, f"League name set to **{value or branding.DEFAULT_LEAGUE_NAME}**.")

    async def _offers(self, interaction: discord.Interaction) -> None:
        tx = self.config.get("transactions", {}) or {}
        modal = FormModal("Signing offers", self._submit_offers)
        modal.expiry = modal.text("Offers expire after (minutes, 1-1440)", default=str(tx.get("default_expiry_minutes", 60)), max_length=4)
        modal.approval = modal.choice(
            "League approval for signings",
            [
                discord.SelectOption(label="Not required", value="off", default=not tx.get("require_league_approval")),
                discord.SelectOption(label="Required", value="on", default=bool(tx.get("require_league_approval"))),
            ],
        )
        await interaction.response.send_modal(modal)

    async def _submit_offers(self, interaction: discord.Interaction, modal: FormModal) -> None:
        try:
            minutes = parse_int(modal.expiry.value, "Offer expiry", minimum=1, maximum=1440)
        except LeagueRuleError as exc:
            self.notice = f"❌ {exc.message}"
            await self.show(interaction)
            return
        tx = dict(self.config.get("transactions", {}) or {})
        tx["default_expiry_minutes"] = minutes
        tx["require_league_approval"] = modal.approval.values[0] == "on"
        self.config["transactions"] = tx
        await self._save(interaction, "Signing offer settings saved.")


async def setup(bot: Any) -> None:  # helper module; nothing to register
    pass
