"""Render every generated image with dummy data, so the look can be checked without Discord or a database.

    python scripts/preview_images.py            # writes preview_output/*.png and preview_output/index.html
    python scripts/preview_images.py --open     # also opens the gallery in your browser
    python scripts/preview_images.py --out /tmp/previews
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.demo_gallery import build  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "preview_output", help="folder for the images (default: preview_output)")
    parser.add_argument("--open", action="store_true", help="open the gallery in the default browser")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    figures, failed = [], []
    for filename, title, buffer in build():
        if buffer is None:
            failed.append(filename)
            continue
        (args.out / f"{filename}.png").write_bytes(buffer.getvalue())
        figures.append(f'<figure><figcaption>{html.escape(title)}</figcaption><img src="{filename}.png" alt="{html.escape(title)}"></figure>')
        print(f"wrote {args.out / (filename + '.png')}")

    gallery = args.out / "index.html"
    gallery.write_text(
        "<!doctype html><meta charset=utf-8><title>Touchline image previews</title>"
        "<style>body{background:#1e1f22;color:#f2f3f5;font:14px sans-serif;margin:24px}"
        "figure{margin:0 0 32px}figcaption{margin-bottom:8px;color:#b5bac1}img{max-width:100%}</style>"
        + "".join(figures),
        encoding="utf-8",
    )
    print(f"\ngallery: {gallery}")
    if failed:
        print("FAILED to render:", ", ".join(failed))
        return 1
    if args.open:
        webbrowser.open(gallery.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
