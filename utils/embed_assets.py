"""Helpers for loading team/league assets and wiring up embeds."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

import discord

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = ROOT_DIR / "images"
TEAMS_FILE = ROOT_DIR / "teams.json"
EMOJIS_FILE = ROOT_DIR / "emojis.json"
TEAM_IMAGE_SUFFIX = ".png"
LEAGUE_IMAGE_SUFFIX = ".png"

_teams_by_code: Dict[str, Dict[str, str]] = {}
_league_emojis: Dict[str, str] = {}


def _load_teams() -> None:
    global _teams_by_code
    if _teams_by_code:
        return
    try:
        with TEAMS_FILE.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        logger.warning("teams.json not found; team assets disabled")
        return
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode teams.json: %s", exc)
        return

    normalized: Dict[str, Dict[str, str]] = {}
    for region, teams in (raw or {}).items():
        for name, data in (teams or {}).items():
            code = data.get("code")
            if not code:
                continue
            normalized[code.lower()] = {
                "code": code,
                "display_name": name,
                "emoji": data.get("emoji"),
                "region": region,
            }
    _teams_by_code = normalized


def _load_league_emojis() -> None:
    global _league_emojis
    if _league_emojis:
        return
    try:
        with EMOJIS_FILE.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        logger.warning("emojis.json not found; league emojis disabled")
        return
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode emojis.json: %s", exc)
        return

    leagues = (raw or {}).get("leagues") or {}
    for key, val in leagues.items():
        if key and isinstance(key, str) and isinstance(val, str):
            _league_emojis[key.upper()] = val


def get_team_data(team_code: Optional[str]) -> Optional[Dict[str, str]]:
    if not team_code:
        return None
    _load_teams()
    return _teams_by_code.get(team_code.lower())


def get_team_emoji(team_code: Optional[str]) -> Optional[str]:
    data = get_team_data(team_code)
    return data.get("emoji") if data else None


def get_team_display(team_code: Optional[str]) -> Optional[str]:
    data = get_team_data(team_code)
    return data.get("display_name") if data else None


def _team_image_path(team_code: Optional[str]) -> Optional[Path]:
    if not team_code:
        return None
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
    if not league_code:
        return None
    _load_league_emojis()
    return _league_emojis.get(league_code.upper())


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
    _load_teams()
    return sorted(_teams_by_code.keys())


def get_team_full_info(team_code: Optional[str]) -> Optional[dict]:
    """Get complete team info including code, display name, emoji, region."""
    if not team_code:
        return None
    _load_teams()
    return _teams_by_code.get(team_code.lower())
