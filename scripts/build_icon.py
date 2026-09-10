#!/usr/bin/env python3
"""Generate assets/icons/pulse.ico from the pixel heartbeat design (Pillow)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageColor

ASSETS = Path(__file__).resolve().parent.parent / "pulse_hwm" / "assets" / "icons"
ASSETS.mkdir(parents=True, exist_ok=True)

GRID = [
    "....X...",
    "....X...",
    "...XX...",
    "X..X.XX.",
    "XX.X.X.X",
    ".XXX..XX",
    "..X.....",
    "........",
]

AMBER = (255, 212, 0)
BLACK = (10, 10, 10)
OUTLINE = (46, 46, 46)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(scale: int) -> Image.Image:
    size = 8 * scale
    img = Image.new("RGBA", (size, size), BLACK + (255,))
    for y, row in enumerate(GRID):
        for x, ch in enumerate(row):
            if ch == "X":
                for dy in range(scale):
                    for dx in range(scale):
                        img.putpixel((x * scale + dx, y * scale + dy), AMBER + (255,))
    return img


def main() -> None:
    images = [render(s) for s in SIZES]
    icon_path = ASSETS / "pulse.ico"
    images[0].save(icon_path, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[1:])
    png_path = ASSETS / "pulse.png"
    render(16).resize((256, 256), Image.Resampling.NEAREST).save(png_path)
    print(f"wrote {icon_path} and {png_path}")


if __name__ == "__main__":
    main()
