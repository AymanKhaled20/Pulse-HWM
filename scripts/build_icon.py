#!/usr/bin/env python3
"""Build all Pulse-HWM brand assets from the master logo PNG.

Source: PULSE wordmark + pixel monitor logo (assets/branding/logo.png).
Produces:
  assets/branding/logo.png        full master logo (512px wide)
  assets/icons/pulse.ico          multi-size Windows icon (monitor mark)
  assets/icons/pulse.png          256px icon mark (used by tray/theme code)

The icon is the top band of the master (monitor + stand, no wordmark),
trimmed to its bright pixels and padded to a square on the #0A0A0A bg.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "pulse_hwm" / "assets"
BRANDING = ASSETS / "branding"
ICONS = ASSETS / "icons"
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
BG = (10, 10, 10, 255)


def crop_icon_mark(master: Image.Image) -> Image.Image:
    """Drop the wordmark: the monitor mark lives ABOVE the 'PULSE' text,
    so take a luminance-threshold bbox of the top band."""
    w, h = master.size
    band = master.crop((0, 0, w, int(h * 0.66)))
    mask = band.convert("L").point(lambda v: 255 if v > 18 else 0)
    mark = band.crop(mask.getbbox())
    side = max(mark.size)
    square = Image.new("RGBA", (side, side), BG)
    square.paste(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
    return square


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/build_icon.py <master-logo.png>")
    master = Image.open(sys.argv[1]).convert("RGBA")

    BRANDING.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(exist_ok=True)
    master.resize(
        (512, round(512 * master.height / master.width)),
        Image.Resampling.LANCZOS,
    ).save(BRANDING / "logo.png")

    mark = crop_icon_mark(master)
    mark.resize((256, 256), Image.Resampling.LANCZOS).save(ICONS / "pulse.png")
    mark.resize((256, 256), Image.Resampling.LANCZOS).save(
        ICONS / "pulse.ico",
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
    )
    print(
        f"wrote {BRANDING / 'logo.png'}, {ICONS / 'pulse.ico'}, {ICONS / 'pulse.png'}"
    )


if __name__ == "__main__":
    main()
