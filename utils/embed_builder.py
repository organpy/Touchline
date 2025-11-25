"""
EmbedBuilder: Modular, DRY embed creation with team/league branding, images, and metadata.
Provides standardized embeds for stats, transactions, and other commands.
Leverages teams.json, league emojis, and image assets.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import discord

from utils.team_data_provider import TeamDataProvider
from utils.embed_assets import (
    get_team_logo_file,
    get_league_logo_file,
    get_league_emoji,
)
from utils.team_colors import resolve_color, get_default_color

logger = logging.getLogger(__name__)


class EmbedBuilder:
    """Builder for standardized embeds with team/league branding."""

    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        color: Optional[discord.Color] = None,
        team_code: Optional[str] = None,
        division_code: Optional[str] = None,
        force_color: Optional[str] = None,
        apply_footer: bool = True,
    ):
        """Initialize embed with base properties and unified footer/logo theming.

        Color priority: force_color -> team_code -> division_code -> global default.
        Footer text always 'National Bobble League'. Footer icon uses NBL logo if available.
        Thumbnail intentionally set separately via add_team_image/league methods.
        """
        resolved_int = resolve_color(team_code=team_code, division_code=division_code, force_color=force_color)
        self.embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color(resolved_int) if color is None else color,
        )
        self.files: List[discord.File] = []
        self.thumbnail_url: Optional[str] = None

        if apply_footer:
            # Set footer text only (no attachment icon to avoid extra files)
            self.embed.set_footer(text="National Bobble League")

    # ==================== STYLING ====================

    def add_team_badge(self, team_code: Optional[str]) -> "EmbedBuilder":
        """Add team emoji badge to title."""
        if not team_code:
            return self

        provider = TeamDataProvider()
        emoji = provider.get_team_emoji(team_code)
        if emoji and self.embed.title:
            self.embed.title = f"{emoji} {self.embed.title}"

        return self

    def add_league_badge(self, league_code: Optional[str]) -> "EmbedBuilder":
        """Add league emoji to footer."""
        if not league_code:
            return self

        emoji = get_league_emoji(league_code)
        if not emoji:
            return self

        footer_text = getattr(self.embed.footer, "text", "") or ""
        if footer_text:
            self.embed.set_footer(text=f"{emoji} {footer_text}")
        else:
            self.embed.set_footer(text=emoji)

        return self

    def set_footer_text(self, text: str, icon_url: Optional[str] = None) -> "EmbedBuilder":
        """Set footer text with optional icon."""
        if text:
            self.embed.set_footer(text=text, icon_url=icon_url)
        return self

    def add_timestamp(self) -> "EmbedBuilder":
        """Add current timestamp to embed."""
        self.embed.timestamp = datetime.utcnow()
        return self

    # ==================== FIELDS ====================

    def add_field(
        self,
        name: str,
        value: str,
        inline: bool = True,
    ) -> "EmbedBuilder":
        """Add a field to the embed."""
        self.embed.add_field(name=name, value=value, inline=inline)
        return self

    def add_team_field(
        self,
        team_code: Optional[str],
        label: str = "Team",
        inline: bool = True,
        fallback_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Add formatted team field with code and emoji."""
        provider = TeamDataProvider()
        team = provider.get_team(team_code) if team_code else None

        display_name = (team or {}).get("display_name") or fallback_name
        code_value = (team or {}).get("code") or team_code
        emoji = (team or {}).get("emoji")

        if not display_name and not code_value:
            return self

        value_parts = []
        if emoji:
            value_parts.append(emoji)

        if display_name and code_value:
            value_parts.append(f"{display_name} (`{code_value.upper()}`)")
        elif display_name:
            value_parts.append(display_name)
        elif code_value:
            value_parts.append(f"(`{code_value.upper()}`)")

        value = " ".join(value_parts) if value_parts else None
        if not value:
            return self

        return self.add_field(label, value, inline=inline)

    def add_league_field(
        self,
        league_code: Optional[str],
        league_name: Optional[str] = None,
        label: str = "League",
        inline: bool = True,
    ) -> "EmbedBuilder":
        """Add formatted league field with code and emoji."""
        display = league_name or (league_code.upper() if league_code else None)
        if not display:
            return self

        emoji = get_league_emoji(league_code) if league_code else None
        value = f"{emoji} {display}" if emoji else display

        return self.add_field(label, value, inline=inline)

    def add_season_field(
        self,
        season_name: str,
        label: str = "Season",
        inline: bool = True,
    ) -> "EmbedBuilder":
        """Add season field."""
        return self.add_field(label, season_name, inline=inline)

    def add_player_field(
        self,
        player_name: str,
        label: str = "Player",
        inline: bool = True,
    ) -> "EmbedBuilder":
        """Add player field."""
        return self.add_field(name=label, value=f"**{player_name}**", inline=inline)

    def add_stat_field(
        self,
        stat_name: str,
        stat_value: str,
        rank: Optional[int] = None,
        inline: bool = False,
    ) -> "EmbedBuilder":
        """Add stat field with optional rank."""
        if rank and rank <= 3:
            medals = ["🥇", "🥈", "🥉"]
            field_name = f"{medals[rank - 1]} #{rank}: {stat_name}"
        elif rank:
            field_name = f"#{rank}: {stat_name}"
        else:
            field_name = stat_name

        return self.add_field(field_name, stat_value, inline=inline)

    # ==================== IMAGES & ATTACHMENTS ====================

    def add_team_image(
        self,
        team_code: Optional[str],
        position: str = "thumbnail",
    ) -> "EmbedBuilder":
        """Attach a team image as thumbnail or full image."""
        if not team_code:
            return self

        file = get_team_logo_file(team_code)
        if not file:
            return self

        self.files.append(file)

        if not file.filename:
            return self

        if position == "thumbnail":
            self.embed.set_thumbnail(url=f"attachment://{file.filename}")
        elif position == "image":
            self.embed.set_image(url=f"attachment://{file.filename}")

        return self

    def add_league_image(
        self,
        league_code: Optional[str],
        position: str = "image",
    ) -> "EmbedBuilder":
        """Attach a league image as thumbnail or main image."""
        if not league_code:
            return self

        file = get_league_logo_file(league_code)
        if not file:
            return self

        self.files.append(file)

        if not file.filename:
            return self

        if position == "thumbnail":
            self.embed.set_thumbnail(url=f"attachment://{file.filename}")
        else:
            self.embed.set_image(url=f"attachment://{file.filename}")

        return self

    def set_thumbnail_url(self, filename: str) -> "EmbedBuilder":
        """Set thumbnail from attachment filename."""
        self.embed.set_thumbnail(url=f"attachment://{filename}")
        return self

    # ==================== BUILD & SEND ====================

    def build(self) -> discord.Embed:
        """Return the built embed."""
        return self.embed

    # ==================== THEMING HELPERS ====================

    @staticmethod
    def themed(
        title: str,
        description: Optional[str] = None,
        team_code: Optional[str] = None,
        division_code: Optional[str] = None,
        force_color: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a themed embed applying footer + color resolution.

        Use this where no existing factory fits but theming is desired.
        """
        return EmbedBuilder(
            title=title,
            description=description,
            team_code=team_code,
            division_code=division_code,
            force_color=force_color,
        )

    def build_with_files(self) -> Tuple[discord.Embed, List[discord.File]]:
        """Return embed and files tuple."""
        return self.embed, self.files

    # ==================== FACTORY METHODS ====================

    @staticmethod
    def leaderboard(
        stat_name: str,
        season_name: str,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a leaderboard-style embed."""
        builder = EmbedBuilder(
            title=f"🏆 Leaderboard — {stat_name.title()}",
            color=discord.Color.gold(),
            division_code=league_code,
        )

        if league_code:
            builder.add_league_badge(league_code)
            builder.add_league_field(league_code, league_name)

        builder.add_season_field(season_name)

        builder.add_timestamp()

        return builder

    @staticmethod
    def player_stats(
        player_name: str,
        team_code: Optional[str] = None,
        season_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a player stats embed."""
        builder = EmbedBuilder(
            title=f"📊 {player_name}",
            team_code=team_code,
        )

        if team_code:
            builder.add_team_badge(team_code)
            builder.add_team_field(team_code)

        if season_name:
            builder.add_season_field(season_name)

        builder.add_timestamp()

        return builder

    @staticmethod
    def comparison(
        player1_name: str,
        player2_name: str,
        team1_code: Optional[str] = None,
        team2_code: Optional[str] = None,
        season_name: Optional[str] = None,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a player comparison embed."""
        title = f"⚔️ {player1_name} vs {player2_name}"

        builder = EmbedBuilder(
            title=title,
            division_code=league_code,
        )

        if league_code:
            builder.add_league_badge(league_code)
            builder.add_league_field(league_code, league_name)

        if season_name:
            builder.add_season_field(season_name)

        if team1_code:
            builder.add_team_field(team1_code, label="Player 1 Team", inline=True)

        if team2_code:
            builder.add_team_field(team2_code, label="Player 2 Team", inline=True)

        builder.add_timestamp()

        return builder

    @staticmethod
    def standings(
        season_name: str,
        league_code: Optional[str] = None,
        league_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a standings embed."""
        builder = EmbedBuilder(
            title="📋 Standings",
            division_code=league_code,
        )

        if league_code:
            builder.add_league_badge(league_code)

        builder.add_season_field(season_name)

        if league_code and league_name:
            builder.add_league_field(league_code, league_name)

        builder.add_timestamp()

        return builder

    @staticmethod
    def transaction(
        action: str,
        player_name: str,
        team_code: Optional[str] = None,
        season_name: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a transaction confirmation embed."""
        builder = EmbedBuilder(
            title=f"✅ {action}",
            team_code=team_code,
            force_color="#57F287",  # Discord green success
        )

        builder.add_player_field(player_name)

        if team_code:
            builder.add_team_field(team_code)

        if season_name:
            builder.add_season_field(season_name)

        builder.add_timestamp()

        return builder

    @staticmethod
    def error(
        title: str = "❌ Error",
        description: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create an error embed."""
        return EmbedBuilder(
            title=title,
            description=description,
            force_color="#ED4245",  # Discord red error
        ).add_timestamp()

    @staticmethod
    def success(
        title: str = "✅ Success",
        description: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a success embed."""
        return EmbedBuilder(
            title=title,
            description=description,
            force_color="#57F287",
        ).add_timestamp()

    @staticmethod
    def warning(
        title: str = "⚠️ Warning",
        description: Optional[str] = None,
    ) -> "EmbedBuilder":
        """Create a warning embed with orange color."""
        return EmbedBuilder(
            title=title,
            description=description,
            force_color="#FAA61A",
        ).add_timestamp()

    @staticmethod
    def generic(
        title: str,
        description: Optional[str] = None,
        team_code: Optional[str] = None,
        division_code: Optional[str] = None,
        force_color: Optional[str] = None,
    ) -> "EmbedBuilder":
        """General purpose themed embed respecting color resolution chain."""
        return EmbedBuilder(
            title=title,
            description=description,
            team_code=team_code,
            division_code=division_code,
            force_color=force_color,
        ).add_timestamp()

