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
    build_team_logo_payload,
    build_league_logo_payload,
)

logger = logging.getLogger(__name__)


class EmbedBuilder:
    """Builder for standardized embeds with team/league branding."""

    def __init__(
        self,
        title: str,
        description: Optional[str] = None,
        color: Optional[discord.Color] = None,
    ):
        """Initialize embed with base properties."""
        self.embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blue(),
        )
        self.files: List[discord.File] = []
        self.thumbnail_url: Optional[str] = None

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
    ) -> "EmbedBuilder":
        """Add formatted team field with code and emoji."""
        if not team_code:
            return self

        provider = TeamDataProvider()
        team_name = provider.get_team_name(team_code)
        if not team_name:
            return self

        emoji = provider.get_team_emoji(team_code)
        value = f"{emoji} {team_name} (`{team_code.upper()}`)" if emoji else f"{team_name} (`{team_code.upper()}`)"

        return self.add_field(label, value, inline=inline)

    def add_league_field(
        self,
        league_code: Optional[str],
        league_name: Optional[str] = None,
        label: str = "League",
        inline: bool = True,
    ) -> "EmbedBuilder":
        """Add formatted league field with code and emoji."""
        if not league_code:
            return self

        emoji = get_league_emoji(league_code)
        value = f"{emoji} {league_name or league_code.upper()}" if emoji else (league_name or league_code.upper())

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
        return self.add_field(label=label, value=f"**{player_name}**", inline=inline)

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
        as_thumbnail: bool = True,
    ) -> "EmbedBuilder":
        """
        Add team image.
        If as_thumbnail=True, sets as thumbnail.
        Otherwise, adds to files list.
        """
        if not team_code:
            return self

        file = get_team_logo_file(team_code)
        if not file:
            return self

        self.files.append(file)

        if as_thumbnail and file.filename:
            self.embed.set_thumbnail(url=f"attachment://{file.filename}")

        return self

    def add_league_image(
        self,
        league_code: Optional[str],
        as_thumbnail: bool = False,
    ) -> "EmbedBuilder":
        """
        Add league image.
        If as_thumbnail=True, sets as thumbnail.
        """
        if not league_code:
            return self

        file = get_league_logo_file(league_code)
        if not file:
            return self

        self.files.append(file)

        if as_thumbnail and file.filename:
            self.embed.set_thumbnail(url=f"attachment://{file.filename}")

        return self

    def set_thumbnail_url(self, filename: str) -> "EmbedBuilder":
        """Set thumbnail from attachment filename."""
        self.embed.set_thumbnail(url=f"attachment://{filename}")
        return self

    # ==================== BUILD & SEND ====================

    def build(self) -> discord.Embed:
        """Return the built embed."""
        return self.embed

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
        )

        if league_code:
            builder.add_league_badge(league_code)

        builder.add_season_field(season_name)

        if league_code and league_name:
            builder.add_league_field(league_code, league_name)

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
            color=discord.Color.blue(),
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
    ) -> "EmbedBuilder":
        """Create a player comparison embed."""
        title = f"⚔️ {player1_name} vs {player2_name}"

        builder = EmbedBuilder(
            title=title,
            color=discord.Color.purple(),
        )

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
            color=discord.Color.from_rgb(52, 152, 219),
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
            color=discord.Color.green(),
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
            color=discord.Color.red(),
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
            color=discord.Color.green(),
        ).add_timestamp()
