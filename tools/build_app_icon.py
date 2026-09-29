"""Build the application icon (.ico) from the branding artwork.

The artwork is a rounded tile with a wide transparent margin; Windows shows
icons at the size of their visible content, so the tile is cropped and
scaled to nearly fill every icon size, like other applications' icons.

usage: python tools/build_app_icon.py [source.png] [target.ico]
"""
from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "branding" / "alliance-boss-strategy-icon-v3.png"
TARGET = ROOT / "branding" / "alliance-boss-strategy-icon-v3.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def margin(size: int) -> int:
    """Transparent border per side: none at the smallest sizes, about 3% above."""
    if size <= 32:
        return 0
    if size <= 40:
        return 1
    return round(size * 0.03)


def tile(source: Image.Image) -> Image.Image:
    """The visible tile, centered on a square transparent canvas."""
    image = source.convert("RGBA")
    box = image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    if box is None:
        raise ValueError("the artwork is fully transparent")
    cropped = image.crop(box)
    side = max(cropped.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return square


def frames(source: Image.Image) -> list[Image.Image]:
    content = tile(source)
    result = []
    for size in SIZES:
        inner = size - 2 * margin(size)
        frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        frame.paste(content.resize((inner, inner), Image.Resampling.LANCZOS), (margin(size), margin(size)))
        result.append(frame)
    return result


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else TARGET
    images = frames(Image.open(source))
    largest = images[-1]
    largest.save(target, format="ICO", sizes=[image.size for image in images], append_images=images[:-1])
    print(f"{target}: {', '.join(str(size) for size in SIZES)} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
