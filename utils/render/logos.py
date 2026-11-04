"""Team and division logos: bundled in ``images/`` or uploaded to ``data/logo_cache``, resized once and reused."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[2]
IMAGES_DIR = ROOT_DIR / "images"
LOGO_CACHE_DIR = ROOT_DIR / "data" / "logo_cache"
EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@lru_cache(maxsize=1)
def _index() -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    # Uploaded logos (data/logo_cache) win over bundled images with the same name
    for directory in (IMAGES_DIR, LOGO_CACHE_DIR):
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path.suffix.lower() in EXTENSIONS:
                index[_key(path.stem)] = path
    return index


@lru_cache(maxsize=256)
def _load(path: Path, size: int) -> Optional[Image.Image]:
    try:
        image = Image.open(path).convert("RGBA")
    except Exception:
        return None
    image.thumbnail((size, size), Image.LANCZOS)
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    square.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
    return square


def logo(size: int, *names: Optional[str]) -> Optional[Image.Image]:
    """The first logo found for any of ``names`` (a code or a name), as a ``size`` x ``size`` image.

    The returned image is shared: paste it, do not modify it.
    """
    index = _index()
    for name in names:
        if name and (path := index.get(_key(name))):
            return _load(path, size)
    return None


def clear() -> None:
    """Forget the file index and resized logos, after logos were uploaded or removed."""
    _index.cache_clear()
    _load.cache_clear()
