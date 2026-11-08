"""Fonts, loaded once per size instead of on every render."""

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Poppins at ``size`` (bold or regular); Pillow's default font when the file is missing."""
    try:
        return ImageFont.truetype(str(FONTS_DIR / ("Poppins-Bold.ttf" if bold else "Poppins-Regular.ttf")), size)
    except OSError:
        return ImageFont.load_default(size)
