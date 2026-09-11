"""Contrast check for every ColorTheme in ui/palettes.py.

Evaluates the foreground/background pairs that theme.qss actually uses
(text roles over surfaces and the inverted accent state) against WCAG 2.1
ratios: 4.5 for normal text, 3.0 for bold/large text. Run with an
activated venv:  python scripts/check_theme_contrast.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# the terminal may be cp1252 — report in plain ASCII
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulse_hwm.ui.palettes import COLOR_THEMES  # noqa: E402

PAIRS = [
    # (fg role, bg role, minimum ratio, where it appears in the QSS)
    ("text", "bg", 4.5, "body text on app bg"),
    ("text", "panel", 4.5, "body text on panels/tables"),
    ("muted", "bg", 4.5, "muted labels on app bg"),
    ("muted", "panel", 4.5, "muted units/labels on panels"),
    ("primary", "bg", 3.0, "big titles / chart lines on app bg"),
    ("primary", "panel", 4.5, "section titles on panels"),
    ("highlight", "bg", 3.0, "panel captions on app bg"),
    ("highlight", "panel", 4.5, "tooltips / table headers on panels"),
    ("danger", "panel", 4.5, "danger button text on panel"),
    ("success", "panel", 4.5, "success button text on panel"),
    (
        "bg",
        "primary",
        4.5,
        "text drawn INVERTED on the accent (selected tab, hover button)",
    ),
]


def _srgb(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lin(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _luma(hexstr: str) -> float:
    r, g, b = (_lin(v) for v in _srgb(hexstr))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg: str, bg: str) -> float:
    hi = max(_luma(fg), _luma(bg)) + 0.05
    lo = min(_luma(fg), _luma(bg)) + 0.05
    return hi / lo


def main() -> int:
    total_fail = 0
    for theme in COLOR_THEMES:
        print(f"\n{theme.label} ({theme.id})")
        fail = 0
        for fg, bg, need, label in PAIRS:
            r = ratio(getattr(theme, fg), getattr(theme, bg))
            ok = r >= need
            if not ok:
                fail += 1
            print(
                f"  {'OK  ' if ok else 'FAIL'} {r:3.2f} (need {need})"
                f"  {label:55s} {getattr(theme, fg)} on {getattr(theme, bg)}"
            )
        print(f"  → {len(PAIRS) - fail}/{len(PAIRS)} pass")
        total_fail += fail
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
