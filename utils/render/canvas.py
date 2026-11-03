"""Drawing primitives for the flat card images: the card itself, fitted text, logos and a render cache."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont

from utils.caching import TTLCache
from utils.render import logos
from utils.render.fonts import font
from utils.render.theme import BG, CARD, DIVIDER, RGB, TEXT

PADDING = 24  # space between the image edge and the card
RADIUS = 18  # card corner radius
ACCENT_HEIGHT = 6  # accent strip along the top of the card


def new_image(width: int, height: int, accent: RGB) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    """A dark image with a rounded card on it and a strip of the accent colour along the card's top edge.

    Content starts at ``PADDING + ACCENT_HEIGHT`` from the top.
    """
    image = Image.new("RGB", (width, height), BG)
    box = (PADDING, PADDING, width - PADDING - 1, height - PADDING - 1)

    shape = Image.new("L", image.size, 0)
    ImageDraw.Draw(shape).rounded_rectangle(box, radius=RADIUS, fill=255)
    image.paste(CARD, (0, 0), shape)

    strip = Image.new("L", image.size, 0)
    ImageDraw.Draw(strip).rectangle((0, PADDING, width, PADDING + ACCENT_HEIGHT - 1), fill=255)
    image.paste(accent, (0, 0), ImageChops.multiply(shape, strip))
    return image, ImageDraw.Draw(image)


def fit_text(text: Optional[str], fnt: ImageFont.FreeTypeFont, max_width: int) -> str:
    """``text`` cut with an ellipsis so that it is at most ``max_width`` pixels wide."""
    text = (text or "").strip()
    if fnt.getlength(text) <= max_width:
        return text
    while text and fnt.getlength(f"{text}…") > max_width:
        text = text[:-1].rstrip()
    return f"{text}…" if text else "…"


def draw_logo(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    center: Tuple[int, int],
    size: int,
    names: Sequence[Optional[str]],
    label: Optional[str] = None,
) -> None:
    """A logo centred at ``center``; when none is found, a circle with the first letter of ``label``."""
    cx, cy = center
    picture = logos.logo(size, *names)
    if picture:
        image.paste(picture, (cx - size // 2, cy - size // 2), picture)
        return
    half = size // 2
    draw.ellipse((cx - half, cy - half, cx + half, cy + half), fill=DIVIDER)
    letter = (label or next((n for n in names if n), "?"))[:1].upper()
    draw.text((cx, cy), letter, font=font(max(size // 2, 10), bold=True), fill=TEXT, anchor="mm")


class RenderCache:
    """Finished images kept for a while, keyed by what went into them."""

    def __init__(self, ttl_seconds: int = 600, max_size: int = 32) -> None:
        self._cache = TTLCache(ttl_seconds=ttl_seconds, max_size=max_size)

    @staticmethod
    def key(payload: Any) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[io.BytesIO]:
        data = self._cache.get(key)
        return io.BytesIO(data) if data is not None else None

    def put(self, key: str, image: Image.Image) -> io.BytesIO:
        """Store the image as PNG and return it as a fresh buffer."""
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        self._cache.set(key, data)
        return io.BytesIO(data)

    def clear(self) -> None:
        self._cache.clear()
