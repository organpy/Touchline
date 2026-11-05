"""Team logos: uploaded from the dashboard to Supabase Storage, cached locally for images.

Uploads are normalised to a square PNG, stored in the public ``team-logos`` bucket as
``<team id>.png`` and the public URL is saved on ``teams.logo_url`` (so a website can show them too).
The image generators and embeds look logos up on disk by team code, so every logo is also written to
``data/logo_cache/<code>.png``; ``sync_team_logos`` restores that cache after a restart or deploy.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
LOGO_CACHE_DIR = ROOT_DIR / "data" / "logo_cache"
BUCKET = "team-logos"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
LOGO_SIZE = 512
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class LogoError(Exception):
    """A logo could not be used. ``str(exc)`` is safe to show to users."""


def cached_logo_path(team_code: Optional[str]) -> Optional[Path]:
    """Local copy of a team's uploaded logo, if there is one."""
    if not team_code:
        return None
    path = LOGO_CACHE_DIR / f"{team_code.lower()}.png"
    return path if path.exists() else None


def normalize_logo(data: bytes) -> bytes:
    """Square, transparent-padded PNG of at most LOGO_SIZE pixels."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise LogoError("That file isn't an image Touchline can read (use PNG, JPEG or WebP).") from exc
    image = image.convert("RGBA")
    image.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    side = max(image.width, image.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def clear_image_indexes() -> None:
    """The image generators cache their logo file index; drop it after logos change."""
    from utils.render import logos

    logos.clear()

    from utils import player_card_image_generator

    for module in (player_card_image_generator,):
        module._image_index.cache_clear()


def _write_cache(team_code: str, png: bytes) -> None:
    LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (LOGO_CACHE_DIR / f"{team_code.lower()}.png").write_bytes(png)


def remove_cached_logo(team_code: Optional[str]) -> None:
    path = cached_logo_path(team_code)
    if path:
        path.unlink(missing_ok=True)
        clear_image_indexes()


async def upload_team_logo(supabase: Any, team: dict, attachment: Any) -> str:
    """Store an uploaded ``discord.Attachment`` as the team's logo and return its public URL."""
    content_type = (getattr(attachment, "content_type", None) or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_TYPES:
        raise LogoError("Logos must be PNG, JPEG, WebP or GIF images.")
    if (getattr(attachment, "size", 0) or 0) > MAX_UPLOAD_BYTES:
        raise LogoError("Logos must be 4 MB or smaller.")

    png = normalize_logo(await attachment.read())
    path = f"{team['id']}.png"

    def _upload() -> str:
        bucket = supabase.storage.from_(BUCKET)
        bucket.upload(path, png, {"content-type": "image/png", "upsert": "true", "cache-control": "300"})
        return bucket.get_public_url(path)

    try:
        url = await asyncio.to_thread(_upload)
    except Exception as exc:
        logger.error("Logo upload for team %s failed: %s", team.get("code"), exc)
        raise LogoError(
            f"Couldn't upload to Supabase Storage. Check that a public `{BUCKET}` bucket exists."
        ) from exc

    _write_cache(team["code"], png)
    clear_image_indexes()
    # Bust CDN/Discord caches when the logo is replaced
    return f"{url.split('?')[0]}?v={len(png)}"


async def sync_team_logos(teams: Iterable[dict]) -> int:
    """Download logos that are set in the database but missing locally. Returns how many were fetched."""
    import aiohttp

    missing = [t for t in teams if t.get("logo_url") and t.get("code") and not cached_logo_path(t["code"])]
    if not missing:
        return 0
    fetched = 0
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for team in missing:
            try:
                async with session.get(team["logo_url"]) as response:
                    if response.status != 200:
                        continue
                    _write_cache(team["code"], normalize_logo(await response.read()))
                    fetched += 1
            except Exception as exc:
                logger.warning("Could not fetch logo for %s: %s", team.get("code"), exc)
    if fetched:
        clear_image_indexes()
    return fetched
