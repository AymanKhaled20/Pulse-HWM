from __future__ import annotations

import string
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap

from pulse_hwm.ui.palettes import (
    ColorTheme,
    FontTheme,
    color_theme,
    font_theme,
)

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
QSS_PATH = Path(__file__).resolve().parent / "theme.qss"

# every family referenced by ANY bundled font theme, so a broken install
# can warn about all of them (not just the defaults) at boot
FONT_FAMILIES = ("Silkscreen", "VT323", "Press Start 2P")

# ── live palette ───────────────────────────────────────────────
# These module globals ARE the theme: custom-painted widgets read them at
# paint time (T.PRIMARY etc.), so theme switching rebinding these values
# recolors every painter on the next repaint — no widget has to know.
# set_active_theme() is the only place that mutates them.
BG = "#0A0A0A"
PANEL = "#141414"
PANEL_ALT = "#0F0F0F"
LINE = "#2E2E2E"
PRIMARY = "#FFD400"
HIGHLIGHT = "#FFE873"
DANGER = "#FF3B30"
SUCCESS = "#9BE800"
TEXT = "#E8E8E8"
MUTED = "#6A6A6A"

# Live font roles (mirrors the active FontTheme).
TITLE_FONT = "Press Start 2P"
DISPLAY_FONT = "Silkscreen"
BODY_FONT = "VT323"

# a real FontTheme from the start, so active_font_theme()/tick_font() can be
# safely called before the first set_active_theme (boot order, tests, etc.)
_sizes: FontTheme = font_theme("classic")


def load_fonts(logger=print) -> list[str]:
    """Register every bundled .ttf once per process. Must run before the
    first painting; ThemeManager calls this, and app.py calls it at boot."""
    loaded: list[str] = []
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger(f"[fonts] could not load {path.name}")
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        loaded.extend(families)
    known = {f.split()[0].upper() for f in loaded}
    for family in FONT_FAMILIES:
        if family.upper() not in known and f"{family.split()[0].upper()}" not in known:
            logger(
                f"[fonts] WARNING: family '{family}' not available — fallback font in use"
            )
    return loaded


def active_font_theme() -> FontTheme:
    return _sizes


# hard floor for every rendered font size: pixel-art themes ship px sizes as
# small as 9 (readable on their home grid, tiny on real screens)
MIN_FONT_PX = 14


def tick_font() -> QFont:
    # charts got their own slightly smaller tick text; still ≥ MIN_FONT_PX
    return QFont(BODY_FONT, max(MIN_FONT_PX, _sizes.body_px - 4))


def set_active_theme(color: ColorTheme, fonts: FontTheme) -> None:
    """Rebind the module-level palette + font roles. All painters pick the
    new values up on their next paint; QSS is re-rendered by the manager."""
    global BG, PANEL, PANEL_ALT, LINE, PRIMARY, HIGHLIGHT, DANGER, SUCCESS
    global TEXT, MUTED, TITLE_FONT, DISPLAY_FONT, BODY_FONT, _sizes
    BG = color.bg
    PANEL = color.panel
    PANEL_ALT = color.panel_alt
    LINE = color.line
    PRIMARY = color.primary
    HIGHLIGHT = color.highlight
    DANGER = color.danger
    SUCCESS = color.success
    TEXT = color.text
    MUTED = color.muted
    TITLE_FONT = fonts.title
    DISPLAY_FONT = fonts.display
    BODY_FONT = fonts.body
    _sizes = fonts


def render_qss(color: ColorTheme, fonts: FontTheme) -> str:
    """Fill the theme.qss template from a palette + font pair.

    Strict substitution: an unrendered $TOKEN left in the output would
    silently corrupt the stylesheet, so missing tokens raise instead.
    """
    template = QSS_PATH.read_text(encoding="utf-8")
    subs = {
        "BG": color.bg,
        "PANEL": color.panel,
        "PANEL_ALT": color.panel_alt,
        "LINE": color.line,
        "PRIMARY": color.primary,
        "HIGHLIGHT": color.highlight,
        "DANGER": color.danger,
        "SUCCESS": color.success,
        "TEXT": color.text,
        "MUTED": color.muted,
        "TITLE_FONT": fonts.title,
        "TITLE_PX": max(MIN_FONT_PX, fonts.title_px),
        "DISPLAY_FONT": fonts.display,
        "DISPLAY_PX": max(MIN_FONT_PX, fonts.display_px),
        "BODY_FONT": fonts.body,
        "BODY_PX": max(MIN_FONT_PX, fonts.body_px),
        "HEADER_PX": max(MIN_FONT_PX, fonts.header_px),
    }
    return string.Template(template).substitute(subs)


def load_theme(app, color_id: str | None = None, font_id: str | None = None) -> None:
    """Apply a theme synchronously (used at boot before any window exists)."""
    color = color_theme(color_id) if color_id else color_theme("amber")
    fonts = font_theme(font_id) if font_id else font_theme("classic")
    set_active_theme(color, fonts)
    app.setStyleSheet(render_qss(color, fonts))


def pixel_pixmap(size: int = 32, draw=None) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor(BG))
    painter = QPainter(px)
    painter.fillRect(0, 0, size, size, QColor(BG))
    if draw:
        draw(painter, size)
    painter.end()
    return px


def _grid_draw(grid: list[str], palette: dict[str, str]):
    def painter_fn(painter: QPainter, size: int) -> None:
        rows = len(grid)
        cols = max((len(r) for r in grid), default=0)
        if rows == 0 or cols == 0:
            return
        cell = size / max(rows, cols)
        painter.setPen(Qt.PenStyle.NoPen)
        for y, row in enumerate(grid):
            for x, ch in enumerate(row):
                color = palette.get(ch)
                if color is None:
                    continue
                painter.fillRect(
                    int(x * cell),
                    int(y * cell),
                    max(1, int(cell)),
                    max(1, int(cell)),
                    QColor(color),
                )

    return painter_fn


HEARTBEAT_GRID = [
    "....X...",
    "....X...",
    "...XX...",
    "X..X.XX.",
    "XX.X.X.X",
    ".XXX..XX",
    "..X.....",
    "........",
]

LED_GRID = [
    "XXXXXXXX",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "X......X",
    "XXXXXXXX",
]

APP_ICON_PALETTES = {PRIMARY: "X"}


def app_icon() -> QIcon:
    icon_file = FONTS_DIR.parent / "icons" / "pulse.ico"
    if icon_file.exists():
        return QIcon(str(icon_file))
    px = pixel_pixmap(64, _grid_draw(HEARTBEAT_GRID, {"X": PRIMARY}))
    return QIcon(px)


def status_icon(state: str) -> QIcon:
    color = {"ok": SUCCESS, "warn": PRIMARY, "error": DANGER}.get(state, MUTED)

    def fill(painter: QPainter, size: int) -> None:
        painter.setPen(QPen(QColor(PRIMARY), 2))
        painter.drawRect(1, 1, size - 2, size - 2)
        painter.fillRect(4, 4, size - 8, size - 8, QColor(color))

    px = pixel_pixmap(32, fill)
    return QIcon(px)


def led_pixmap(on: bool, color: str, size: int = 20) -> QPixmap:
    def paint(p: QPainter, s: int) -> None:
        if on:
            p.fillRect(2, 2, s - 4, s - 4, QColor(color))
            p.fillRect(2, 2, (s - 4) // 2, (s - 4) // 4, QColor(HIGHLIGHT))
        else:
            p.fillRect(2, 2, s - 4, s - 4, QColor(PANEL_ALT))
            p.fillRect(2, 2, s - 4, s - 4, QColor(LINE))

    return pixel_pixmap(size, paint)
