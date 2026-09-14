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

# UI font scale: the BODY base size the user picks in THEMES (14–18px).
# switching a font theme swaps the FAMILY only, never the size; the chosen
# base instead scales EVERY role proportionally, so the layout keeps its
# shape at any size.
BASE_BODY_PX = 18  # shipped default
_body_px = BASE_BODY_PX

# ── UI chrome scale ────────────────────────────────────────────
# ONE multiplier for all non-font geometry: paddings, borders, scrollbar
# widths, fixed widget sizes, margins, window minimum size. Fonts keep
# their own knob (body_px) so a font-size choice never double-scales.
# Expressed as an integer PERCENT in settings (75–125), stored as a
# float multiplier here. s() is the only helper widgets may use — every
# hardcoded px in a widget must go through it (or a token in theme.qss).
UI_SCALE_MIN = 0.75
UI_SCALE_MAX = 1.25
_ui_scale = 1.0


def ui_scale() -> float:
    return _ui_scale


def set_ui_scale(scale: float) -> None:
    global _ui_scale
    _ui_scale = max(UI_SCALE_MIN, min(UI_SCALE_MAX, float(scale)))


def s(px: int | float) -> int:
    """Scale a raw pixel value by the UI scale (never below 1px)."""
    return max(1, round(px * _ui_scale))


def auto_ui_scale_for_height(screen_h: int) -> int:
    """Percent scale picked at first boot from the screen height (pure).
    Small laptop panels need a compact shell; desktops get the default."""
    if screen_h <= 768:
        return 75
    if screen_h <= 900:
        return 90
    return 100


# integer px per role — QSS emits these as "Npx" (a unitless "font-size: 24;"
# is a QSS parse error and Qt silently drops the rule: it once shrank every
# table/UI font to the ~12px system default)
SIZES: dict[str, int] = {}

# chrome geometry (px, scaled by _ui_scale) — consumed by render_qss()
CHROME: dict[str, int] = {}


def _rebuild_chrome() -> None:
    """Derive every chrome (non-font) size from the UI scale."""
    s = _ui_scale
    b = max(1, round(2 * s))  # the thematic 2px border
    CHROME.update(
        {
            "border": b,
            "border1": max(1, round(1 * s)),
            "spacing": max(2, round(2 * s)),  # letter-spacing, separators
            "tab_pad_v": max(3, round(8 * s)),
            "tab_pad_h": max(6, round(18 * s)),
            "btn_pad_v": max(3, round(7 * s)),
            "btn_pad_h": max(6, round(14 * s)),
            "in_pad_v": max(2, round(4 * s)),
            "in_pad_h": max(4, round(8 * s)),
            "scroll": max(8, round(14 * s)),
            "handle": max(12, round(24 * s)),
            "indicator": max(10, round(16 * s)),
            "spin_btn": max(10, round(16 * s)),
            "combo_dd": max(14, round(24 * s)),
            "pad2": max(2, round(2 * s)),
            "pad6": max(3, round(6 * s)),
            "menu_pad_v": max(3, round(6 * s)),
            "menu_pad_h": max(16, round(28 * s)),
        }
    )


def _rebuild_sizes() -> None:
    """Derive every role size from the base body size (ratios of 18px)."""

    def scaled(base: int) -> int:
        return max(MIN_FONT_PX, round(_body_px * base / BASE_BODY_PX))

    SIZES.update(
        {
            "title": scaled(16),  # window/section headlines
            "display": scaled(14),  # labels, tabs, buttons, table headers
            "body": max(MIN_FONT_PX, _body_px),
            "tick": max(MIN_FONT_PX, _body_px - 4),
            "table": scaled(24),  # TOP PROCESSES-style tables
            "table_lg": scaled(30),  # the dedicated PROCESSES tab tree
            "stat": scaled(26),  # big dashboard numbers
        }
    )


_rebuild_sizes()


def body_px() -> int:
    return _body_px


def set_body_px(px: int) -> None:
    """New UI base size (clamped 14–18); every role scales proportionally."""
    global _body_px
    _body_px = max(MIN_FONT_PX, min(BASE_BODY_PX, int(px)))
    _rebuild_sizes()


def set_ui_scale_percent(pct: int) -> None:
    """Set chrome scale from the persisted percent (75–125); rebuild chrome."""
    set_ui_scale(int(pct) / 100.0)
    _rebuild_chrome()


_rebuild_chrome()


def tick_font() -> QFont:
    # setPixelSize, NOT QFont(family, n): the latter is POINT size
    # (n pt ≈ 1.33n px) which silently over-sized the axes.
    f = QFont(BODY_FONT)
    f.setPixelSize(SIZES["tick"])
    return f


def table_font(theme: FontTheme | None = None) -> QFont:
    """Tables/trees read much bigger than body text: dense rows at 14px were
    unreadable on the processes tab."""
    f = QFont((theme or _sizes).body)
    f.setPixelSize(SIZES["table"])
    return f


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
        "TITLE_PX": f"{SIZES['title']}px",
        "DISPLAY_FONT": fonts.display,
        "DISPLAY_PX": f"{SIZES['display']}px",
        "BODY_FONT": fonts.body,
        "BODY_PX": f"{SIZES['body']}px",
        # fixed roles — theme switches must never resize the UI (the base
        # size from THEMES scales them, units mandatory: see SIZES comment)
        "TABLE_PX": f"{SIZES['table']}px",
        "TABLE_PX_LG": f"{SIZES['table_lg']}px",
        "STAT_PX": f"{SIZES['stat']}px",
        "HEADER_PX": f"{SIZES['display']}px",
        # chrome geometry (scaled by UI scale — chrome tokens, see CHROME)
        "BORDER": f"{CHROME['border']}px",
        "BORDER1": f"{CHROME['border1']}px",
        "SPACING": f"{CHROME['spacing']}px",
        "TAB_PAD_V": f"{CHROME['tab_pad_v']}px",
        "TAB_PAD_H": f"{CHROME['tab_pad_h']}px",
        "BTN_PAD_V": f"{CHROME['btn_pad_v']}px",
        "BTN_PAD_H": f"{CHROME['btn_pad_h']}px",
        "BTN_PRESS_V": f"{CHROME['btn_pad_v'] + 1}px",
        "BTN_PRESS_H": f"{max(2, CHROME['btn_pad_h'] - 1)}px",
        "IN_PAD_V": f"{CHROME['in_pad_v']}px",
        "IN_PAD_H": f"{CHROME['in_pad_h']}px",
        "SCROLL": f"{CHROME['scroll']}px",
        "HANDLE": f"{CHROME['handle']}px",
        "INDICATOR": f"{CHROME['indicator']}px",
        "SPIN_BTN": f"{CHROME['spin_btn']}px",
        "COMBO_DD": f"{CHROME['combo_dd']}px",
        "PAD2": f"{CHROME['pad2']}px",
        "PAD6": f"{CHROME['pad6']}px",
        "MENU_PAD_V": f"{CHROME['menu_pad_v']}px",
        "MENU_PAD_H": f"{CHROME['menu_pad_h']}px",
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
