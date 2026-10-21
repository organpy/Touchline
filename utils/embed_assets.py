"""Helpers for loading team/league assets and wiring up embeds."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import discord

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = ROOT_DIR / "images"
TEAM_IMAGE_SUFFIX = ".png"
LEAGUE_IMAGE_SUFFIX = ".png"


def get_team_data(team_code: Optional[str]) -> Optional[Dict[str, str]]:
    """{code, display_name, emoji, region} for a team (from the team cache)."""
    from utils.team_data_provider import TeamDataProvider

    return TeamDataProvider.get_team(team_code)


def get_team_emoji(team_code: Optional[str]) -> Optional[str]:
    data = get_team_data(team_code)
    return (data.get("emoji") or None) if data else None


def get_team_display(team_code: Optional[str]) -> Optional[str]:
    data = get_team_data(team_code)
    return data.get("display_name") if data else None


def _team_image_path(team_code: Optional[str]) -> Optional[Path]:
    if not team_code:
        return None
    from utils.logo_store import cached_logo_path

    uploaded = cached_logo_path(team_code)
    if uploaded:
        return uploaded
    candidate = IMAGES_DIR / f"{team_code.lower()}{TEAM_IMAGE_SUFFIX}"
    if candidate.exists():
        return candidate
    return None


def get_team_logo_file(team_code: Optional[str], filename: Optional[str] = None) -> Optional[discord.File]:
    path = _team_image_path(team_code)
    if not path:
        return None
    if not filename:
        filename = f"{team_code.lower()}_logo.png"
    try:
        return discord.File(path, filename=filename)
    except Exception as exc:
        logger.warning("Failed to load team image %s: %s", path, exc)
        return None


def get_league_emoji(league_code: Optional[str]) -> Optional[str]:
    """The division's emoji (set in the dashboard), looked up by code."""
    if not league_code:
        return None
    from utils.league_cache import LeagueCache

    division = LeagueCache.find_division(league_code)
    return (division or {}).get("emoji") or None


def _league_image_path(league_code: Optional[str]) -> Optional[Path]:
    if not league_code:
        return None
    candidate = IMAGES_DIR / f"{league_code.upper()}{LEAGUE_IMAGE_SUFFIX}"
    if candidate.exists():
        return candidate
    return None


def get_league_logo_file(league_code: Optional[str], filename: Optional[str] = None) -> Optional[discord.File]:
    path = _league_image_path(league_code)
    if not path:
        return None
    if not filename:
        filename = f"{league_code.upper()}_logo.png"
    try:
        return discord.File(path, filename=filename)
    except Exception as exc:
        logger.warning("Failed to load league image %s: %s", path, exc)
        return None


def apply_team_badge(embed: discord.Embed, team_code: Optional[str]) -> None:
    emoji = get_team_emoji(team_code)
    if emoji and embed.title:
        embed.title = f"{emoji} {embed.title}"


def target_thumbnail(embed: discord.Embed, filename: str) -> None:
    embed.set_thumbnail(url=f"attachment://{filename}")


def apply_league_badge(embed: discord.Embed, league_code: Optional[str]) -> None:
    emoji = get_league_emoji(league_code)
    if not emoji:
        return
    footer_text = getattr(embed.footer, "text", "") or ""
    if footer_text:
        embed.set_footer(text=f"{emoji} {footer_text}")
    else:
        embed.set_footer(text=emoji)


def build_team_logo_payload(team_code: Optional[str], suffix: str = "logo") -> tuple[list[discord.File], Optional[str]]:
    if not team_code:
        return [], None
    filename = f"{team_code.lower()}_{suffix}.png"
    file = get_team_logo_file(team_code, filename=filename)
    if file:
        return [file], file.filename
    return [], None


def build_league_logo_payload(league_code: Optional[str], suffix: str = "logo") -> tuple[list[discord.File], Optional[str]]:
    """Build payload for league logo (file + filename for attachments)."""
    if not league_code:
        return [], None
    filename = f"{league_code.upper()}_{suffix}.png"
    file = get_league_logo_file(league_code, filename=filename)
    if file:
        return [file], file.filename
    return [], None


def get_all_team_codes() -> list[str]:
    """Get all team codes (flat list, sorted)."""
    from utils.team_data_provider import TeamDataProvider

    return TeamDataProvider.get_all_codes()


def get_team_full_info(team_code: Optional[str]) -> Optional[dict]:
    """Get complete team info including code, display name, emoji, division code (region)."""
    return get_team_data(team_code)
